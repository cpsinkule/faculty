"""Course templates → immutable versions → term issues → membership cards.

All durable local data lives in the shared catalog and its verified nightly
backup. There are no new payload directories. Only explicitly personalized
schema values leave Faculty; enrollment IDs and raw UINs are never published.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import time
import uuid

from werkzeug.exceptions import Conflict, HTTPException, NotFound, ServiceUnavailable

from canonical_json import encoded_strict as encoded
from catalog_migrations import apply_sql, migration_transaction
from certified_archive import certified, facts as archive_facts, uncertified
from course_storage import timestamp
from rosters import connect
from .mail import CardMailer
from .remote import RemoteClient
from .schema import VARIABLES, personalize, settled_status, unchecked_dropped, validate_schema, with_choices
from scheduling.clock import CAMPUS_ZONE

POLICIES = {'once', 'until_due', 'unlimited', 'instructor_unlock'}
STATUSES = {'issued', 'opened', 'started', 'submitted', 'verified', 'needs_review', 'expired', 'revoked'}
DELIVERY_PROBLEMS = ('failed', 'unconfirmed')
EXAM_OVER = 'exam_over'  # the invalidation reason of a seating card closed after its exam


def closed_statuses(db, card_ids):
    """RESPONSE-CARDS "SEATING CARDS CLOSE AFTER THE EXAM" › THEY KEEP THEIR STATUS (requirement, 2026-10-23): {card ID: status} for each of these cards that closed
    at midnight after its exam, as it stood when it closed: its current submission's settled status, or "closed" when it was never
    answered. A card revoked any other way (a seat change, a Revoke) is absent and stays revoked."""
    ids, out = sorted(set(card_ids)), {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        for card_id, submission_json in db.execute(f"""SELECT x.card_id, s.submission_json FROM response_card_invalidations x
                JOIN response_card_recipients r ON r.id = x.card_id LEFT JOIN response_card_submissions s ON s.id = r.current_submission_id
                WHERE x.reason = ? AND x.card_id IN ({','.join('?' * len(chunk))})""", [EXAM_OVER, *chunk]):
            submission = json.loads(submission_json) if submission_json else None
            out[card_id] = settled_status(submission.get('status'), submission) if submission else 'closed'
    return out


def dr_notice_day(snapshot_json):
    """The DR exam date a DR testing notice states (its `confirm_dr_date` check), else None."""
    fields = json.loads(snapshot_json).get('schema', {}).get('fields', [])
    return next(((field.get('verification') or {}).get('expected') for field in fields if field.get('id') == 'confirm_dr_date'), None)


def exam_over(db, card_ids, now=None):
    """RESPONSE-CARDS "NO FLAGS AFTER THE EXAM" (requirement, 2026-10-23): the given cards whose exam is over, from 12:00 am campus time the day after it. A card's exam day is the
    latest of its chart's exam date (the chart it was issued from, else every chart of its issue's assessment and term) and, on a DR
    notice, the DR exam date it states. A card with neither has no exam day and is never over."""
    today = (now or datetime.now(timezone.utc)).astimezone(CAMPUS_ZONE).date().isoformat()
    ids, over = sorted(set(card_ids)), set()
    if not ids or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='seating_charts'").fetchone():
        return over
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        # Exam dates are stored as YYYY-MM-DD, so text order is date order.
        for card_id, snapshot_json, chart_day in db.execute(f"""SELECT r.id, r.snapshot_json, (SELECT MAX(c.exam_date) FROM seating_charts c
                WHERE c.exam_date!='' AND (c.id=i.seating_chart_id OR (i.seating_chart_id IS NULL AND c.term_id=i.term_id AND c.assignment_id=i.assignment_id)))
                FROM response_card_recipients r JOIN response_card_issues i ON i.id=r.issue_id WHERE r.id IN ({','.join('?' * len(chunk))})""", chunk):
            days = [day for day in (chart_day, dr_notice_day(snapshot_json)) if isinstance(day, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', day)]
            if days and max(days) < today:
                over.add(card_id)
    return over


def delivery_problem(card, invalidated=False):
    """An emailed invitation whose latest delivery failed or was not confirmed while the card is still unopened, unanswered
    and not replaced: the one card state the instructor may want to resend or hand over another way (Home's card rule)."""
    return (not invalidated and card['status'] == 'issued' and not card['current_submission_id']
            and card['delivery_status'] in DELIVERY_PROBLEMS)


def long_date(value):
    """2026-09-25 → Friday, September 25, 2026 (unchanged when it is not an ISO date)."""
    try:
        day = datetime.strptime(value, '%Y-%m-%d')
    except (TypeError, ValueError):
        return value
    return f"{day.strftime('%A, %B')} {day.day}, {day.year}"


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def identifier():
    return str(uuid.uuid4())


def text(value, label, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'{label} is required and must be at most {maximum} characters.')
    return value.strip()


def instant(value, label):
    if value in (None, ''):
        return None
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError(f'{label} must be a timestamp with a timezone.')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    except ValueError:
        raise ValueError(f'{label} must be a timestamp with a timezone.') from None


def id_list(value, label, required=False):
    if not isinstance(value, list) or len(value) > 2000 or any(not isinstance(v, str) or not v or len(v) > 100 for v in value):
        raise ValueError(f'Choose valid {label}.')
    result = sorted(set(value))
    if required and not result:
        raise ValueError(f'Choose at least one {label}.')
    return result


class ResponseCardService:
    def __init__(self, rosters, seating=None, remote=None):
        self.rosters, self.path, self.seating = rosters, rosters.path, seating
        self.remote = remote or RemoteClient()
        self.mail = CardMailer(self)
        with rosters.lock(), connect(self.path) as db, migration_transaction(db):
            legacy = (bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='response_card_templates'").fetchone())
                      and not db.execute("SELECT 1 FROM schema_migrations WHERE namespace='response_cards' LIMIT 1").fetchone())
            self._retire_single_invitation_mail(db)
            apply_sql(db, 'response_cards', '001_schema', Path(__file__).with_name('migration.sql').read_text(encoding='utf-8'), adopt=legacy)
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='response_card_mail_legacy'").fetchone():
                # Every earlier row keeps its ID, status, and receipt; a repeated start after an interruption copies nothing twice.
                columns = ','.join(row['name'] for row in db.execute('PRAGMA table_info(response_card_mail_legacy)'))
                db.execute(f'INSERT OR IGNORE INTO response_card_mail({columns}) SELECT {columns} FROM response_card_mail_legacy')
                db.execute('DROP TABLE response_card_mail_legacy')
            if 'not_before' not in {row['name'] for row in db.execute('PRAGMA table_info(response_card_mail)')}:
                db.execute('ALTER TABLE response_card_mail ADD COLUMN not_before INTEGER NOT NULL DEFAULT 0')
            for column in ('provider', 'gmail_sender'):
                if column not in {row['name'] for row in db.execute('PRAGMA table_info(response_card_mail)')}:
                    db.execute(f"ALTER TABLE response_card_mail ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            # After the legacy copy and the columns: the index belongs to the current table.
            apply_sql(db, 'response_cards', '002_mail_card_index', Path(__file__).with_name('002_mail_card_index.sql').read_text(encoding='utf-8'))
            apply_sql(db, 'response_cards', '003_reminders', Path(__file__).with_name('003_reminders.sql').read_text(encoding='utf-8'))
            if self.mail.provider:
                db.execute("UPDATE response_card_mail SET provider=?,gmail_sender=? WHERE status='pending' AND provider=''",
                           (self.mail.provider, self.mail.gmail.email if self.mail.provider == 'gmail' else ''))
            for table in ('response_card_versions', 'response_card_submissions', 'response_card_events'):
                for action in ('UPDATE', 'DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Response Card history is immutable'); END")
            db.execute("""CREATE TRIGGER IF NOT EXISTS response_issue_immutable BEFORE UPDATE OF id,version_id,term_id,assignment_id,context_json,policy,due_at,expires_at,request_id,request_hash,created_at ON response_card_issues
                       BEGIN SELECT RAISE(ABORT,'Response Card issues are immutable'); END""")
            for table in ('response_card_templates', 'response_card_issues', 'response_card_recipients'):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_retained BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'Response Cards are retained'); END")

    @staticmethod
    def _retire_single_invitation_mail(db):
        """Catalogs from before manual resends allowed one invitation per card (a table-level UNIQUE).

        Renaming that table lets migration.sql create the current one; __init__ then copies
        the rows across. Its queue index is dropped first so the fresh index gets the name.
        """
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='response_card_mail'").fetchone():
            return
        if any(row['origin'] == 'u' for row in db.execute('PRAGMA index_list(response_card_mail)')):
            db.execute('DROP INDEX IF EXISTS response_card_mail_queue')
            db.execute('ALTER TABLE response_card_mail RENAME TO response_card_mail_legacy')

    @staticmethod
    def _require(db, table, key):
        row = db.execute(f'SELECT * FROM {table} WHERE id=?', (key,)).fetchone()
        if row is None:
            raise NotFound('This Response Card resource does not exist.')
        return dict(row)

    @staticmethod
    def _template(row):
        item = dict(row)
        item['schema'] = json.loads(item.pop('schema_json'))
        return item

    @staticmethod
    def _managed(db, items):
        """Mark the templates Faculty itself keeps for exam notices (read-only `managed`: 'seating' or 'exams', else '').

        Seating finds its templates by name (seating.MANAGED_TEMPLATES) and the Exams page records its own; both stay
        ordinary course templates the instructor may edit. Imported here, lazily, only for their names."""
        from seating import MANAGED_TEMPLATES
        seating_names = {spec['name'] for spec in MANAGED_TEMPLATES.values()}
        exams = set()
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='exam_managed_templates'").fetchone():
            exams = {row[0] for row in db.execute('SELECT template_id FROM exam_managed_templates')}
        for item in items:
            item['managed'] = 'exams' if item['id'] in exams else 'seating' if item['name'] in seating_names else ''
        return items

    def templates(self, definition_id=None):
        with connect(self.path) as db:
            return self._managed(db, [self._template(row) for row in db.execute('SELECT * FROM response_card_templates WHERE (? IS NULL OR definition_id=?) ORDER BY updated_at DESC,id', (definition_id, definition_id))])

    def template(self, key):
        with connect(self.path) as db:
            item = self._managed(db, [self._template(self._require(db, 'response_card_templates', key))])[0]
            item['versions'] = [self._template(row) for row in db.execute('SELECT * FROM response_card_versions WHERE template_id=? ORDER BY number DESC', (key,))]
            return item

    def save_template(self, data, key=None):
        name, schema = text(data.get('name'), 'Template name'), validate_schema(data.get('schema'))
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            if key:
                row = self._require(db, 'response_card_templates', key)
                if type(data.get('version')) is not int or data['version'] != row['version']:
                    raise Conflict('This template changed. Reload it before saving.')
                if data.get('definition_id', row['definition_id']) != row['definition_id']:
                    raise ValueError('A template stays with its original course code.')
                db.execute('UPDATE response_card_templates SET name=?,schema_json=?,version=version+1,updated_at=? WHERE id=?', (name, encoded(schema), timestamp(), key))
            else:
                definition = text(data.get('definition_id'), 'Course definition')
                self._require(db, 'course_definitions', definition)
                key = identifier()
                db.execute('INSERT INTO response_card_templates(id,definition_id,name,schema_json,created_at,updated_at) VALUES(?,?,?,?,?,?)', (key, definition, name, encoded(schema), timestamp(), timestamp()))
        return self.template(key)

    def freeze(self, key, revision):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._require(db, 'response_card_templates', key)
            if type(revision) is not int or revision != row['version']:
                raise Conflict('This template changed. Reload it before freezing a version.')
            prior = db.execute('SELECT * FROM response_card_versions WHERE template_id=? AND template_revision=?', (key, revision)).fetchone()
            if prior:
                return self._template(prior)
            count = db.execute('SELECT COALESCE(MAX(number),0)+1 FROM response_card_versions WHERE template_id=?', (key,)).fetchone()[0]
            version_id = identifier()
            db.execute('INSERT INTO response_card_versions VALUES(?,?,?,?,?,?)', (version_id, key, count, revision, row['schema_json'], timestamp()))
            return self._template(self._require(db, 'response_card_versions', version_id))

    def _students(self, db, term, sections):
        # Caller holds lifecycle lock. Payloads are read only through the owner manager.
        with self.rosters.files(term) as root:
            payload = self.rosters.read_payload(root, term['roster_initialized'])
        result = []
        dropped = {(r[0], r[1]) for r in db.execute('SELECT section_id,enrollment_id FROM roster_dropped')}
        # A Q drop or withdrawal on the registrar's roster (requirement, 2026-10-15) is issued no card, as a dropped student is not.
        dropped |= {(r[0], r[1]) for r in db.execute('SELECT section_id,enrollment_id FROM roster_leaving')}
        for section in sections:
            saved = payload['sections'].get(section['id'], {}).get('students', {})
            for member in db.execute('SELECT enrollment_id FROM roster_memberships WHERE section_id=? ORDER BY enrollment_id', (section['id'],)):
                record = saved.get(member['enrollment_id'])
                if not record:
                    raise Conflict('A roster membership has no saved student record. Repair its roster before issuing cards.')
                fields = {str(k).casefold().strip(): str(v).strip() for k, v in record.get('fields', {}).items()}
                name = fields.get('name') or ' '.join(filter(None, [fields.get('first name'), fields.get('last name')]))
                if not name:
                    raise ValueError('A student has no name in the saved roster.')
                last, first = (part.strip() for part in name.split(',', 1)) if ',' in name else (' '.join(name.split()[1:]), name.split()[0])
                first = fields.get('first name', first)
                last = fields.get('last name', last)
                result.append({'enrollment_id': member['enrollment_id'], 'section_id': section['id'], 'section_number': section['number'],
                               'name': name, 'email': fields.get('email') or fields.get('email address') or fields.get('e-mail') or '',
                               'first_name': first, 'last_name': last, 'full_name': ' '.join(filter(None, [first, last])), 'uin': record['uin'],
                               'dropped': (section['id'], member['enrollment_id']) in dropped})
        return result

    def context(self, term_id=None):
        with self.rosters.lock(), connect(self.path) as db:
            terms = [dict(r) for r in db.execute('SELECT * FROM course_terms WHERE deleted_at IS NULL ORDER BY is_current DESC,year DESC,code,term')]
            sections = [dict(r) for r in db.execute('SELECT s.*, (SELECT COUNT(*) FROM roster_current m WHERE m.section_id=s.id) AS count FROM course_sections s ORDER BY number')]
            # Only currently listed sections are offered; retained histories stay accessible through issues.
            visible = {t['id']: json.loads(t['sections']) for t in terms}
            sections = [s for s in sections if s['number'] in visible.get(s['term_id'], [])]
            students = []
            if term_id:
                term = self._require(db, 'course_terms', term_id)
                students = [{k: s[k] for k in ('enrollment_id', 'section_id', 'section_number', 'name', 'email')}
                            for s in self._students(db, term, [s for s in sections if s['term_id'] == term_id]) if not s['dropped']]
            charts = [dict(r) for r in db.execute('SELECT c.id,c.assignment_id,c.term_id,r.name room_name FROM seating_charts c JOIN rooms r ON r.id=c.room_id')]
            for chart in charts:
                chart['section_ids'] = [r[0] for r in db.execute('SELECT section_id FROM seating_chart_sections WHERE chart_id=?', (chart['id'],))]
            sync = db.execute('SELECT synced_at,error FROM response_card_sync WHERE id=1').fetchone()
            return {'configured': self.remote.config.enabled, 'public_url': self.remote.config.url, 'mail_configured': self.mail.enabled,
                    'mail': self.mail.status(),
                    # The last successful reconciliation; the deployed timer syncs every minute (read-only, UX overhaul).
                    'sync': {'synced_at': sync['synced_at'] if sync else None, 'error': sync['error'] if sync else ''},
                    'definitions': [dict(r) for r in db.execute('SELECT id,code FROM course_definitions ORDER BY code')],
                    'terms': [{**{k: t[k] for k in ('id', 'definition_id', 'code', 'name', 'term', 'term_other', 'year', 'is_current')},
                               'label': f"{t['term_other'] if t['term'] == 'Other' else t['term']} {t['year']}"} for t in terms],
                    'sections': sections, 'students': students, 'assignments': [dict(r) for r in db.execute('SELECT id,definition_id,title FROM assignments ORDER BY title')],
                    'seating_charts': charts, 'variables': sorted(VARIABLES)}

    @staticmethod
    def _variables(db, term, student, assignment=None, chart=None, seat=None, room=None):
        """Build live personalization values from an already-validated context.

        Callers hold the roster lifecycle lock and read students through verified
        roster storage. This private mapping includes the raw UIN solely for
        schema personalization; it must not be returned or exported. Exam-workflow
        release/decision values are added by the caller after their own checks.
        """
        variables = {f'student.{k}': student[k] for k in ('name', 'full_name', 'first_name', 'last_name', 'email', 'uin')}
        variables.update({'student.section': str(student['section_number']), 'course.section': str(student['section_number']),
                          'course.code': term['code'], 'course.name': term['name'], 'course.term': term['term_other'] if term['term'] == 'Other' else term['term'], 'course.year': str(term['year'])})
        if assignment:
            variables.update({'assignment.name': assignment['title'], 'exam.name': assignment['title']})
            schedules = db.execute('SELECT r.snapshot_json FROM dr_exam_bookings b JOIN dr_exam_revisions r ON r.id=b.revision_id JOIN dr_exam_assignment_links l ON l.booking_id=b.id WHERE b.section_id=? AND b.enrollment_id=? AND l.assignment_id=?', (student['section_id'], student['enrollment_id'], assignment['id'])).fetchall()
            if len(schedules) == 1:
                schedule = json.loads(schedules[0][0])
                variables.update({'dr_exam.date': schedule['date'], 'dr_exam.time': schedule['start_time'], 'dr_exam.location': schedule['campus']})
        if chart:
            for key, column in (('exam.date', 'exam_date'), ('exam.time', 'exam_time')):
                if chart.get(column):
                    variables[key] = chart[column]
            if chart.get('exam_date'):
                variables['exam.date_long'] = long_date(chart['exam_date'])
        if seat:
            variables.update({'seat.number': seat['label'], 'seating_assignment.seat': seat['label'],
                              'seat.row': seat['row_label'] or str(seat['grid_row']), 'seat.column': str(seat['grid_column']), 'exam.room': room['name']})
        return variables

    def issue(self, data):
        request_id = text(data.get('request_id'), 'Request ID', 100)
        section_ids = id_list(data.get('section_ids'), 'section', True)
        enrollment_ids = id_list(data.get('enrollment_ids', []), 'students')
        policy = data.get('policy', 'once')
        if not isinstance(policy, str) or policy not in POLICIES:
            raise ValueError('Choose a valid submission policy.')
        due, expiry = instant(data.get('due_at'), 'Due date'), instant(data.get('expires_at'), 'Expiry')
        if policy == 'until_due' and not due:
            raise ValueError('A deadline is required when edits are allowed until the deadline.')
        if expiry and due and expiry < due:
            raise ValueError('Expiry cannot be before the deadline.')
        normalized = {'version_id': text(data.get('version_id'), 'Frozen version'), 'term_id': text(data.get('term_id'), 'Term'),
                      'section_ids': section_ids, 'enrollment_ids': enrollment_ids,
                      'assignment_id': text(data['assignment_id'], 'Assignment') if data.get('assignment_id') is not None and data.get('assignment_id') != '' else None,
                      'seating_chart_id': text(data['seating_chart_id'], 'Seating chart') if data.get('seating_chart_id') is not None and data.get('seating_chart_id') != '' else None,
                      'policy': policy, 'due_at': due, 'expires_at': expiry}
        # Additional context references retained exam records, never caller-supplied grades.
        if data.get('exam_event_id'):
            normalized['exam_event_id'] = text(data['exam_event_id'], 'Exam')
            # A quiz makeup booking card names its offer (ABSENCES "MAKEUP BOOKING"): the times and dates come from that record.
            for key in ('feedback_release_id', 'exam_request_id', 'makeup_offer_id'):
                if data.get(key):
                    normalized[key] = text(data[key], key.replace('_', ' '))
            if 'exam_request_id' in normalized:
                if type(data.get('exam_request_version')) is not int:
                    raise ValueError('Supply the reviewed request version.')
                normalized['exam_request_version'] = data['exam_request_version']
        elif data.get('feedback_release_id') or data.get('exam_request_id') or data.get('makeup_offer_id'):
            raise ValueError('Choose the exam for this feedback or request.')
        request_hash = digest(normalized)
        with self.rosters.lock():
            with connect(self.path) as db:
                prior = db.execute('SELECT id,request_hash FROM response_card_issues WHERE request_id=?', (request_id,)).fetchone()
                if prior:
                    if prior['request_hash'] != request_hash:
                        raise Conflict('This issue request was already used with different settings.')
                    return self.get_issue(prior['id'], db=db)
                term = self._require(db, 'course_terms', normalized['term_id'])
                if not term['is_current'] or term['deleted_at']:
                    raise ValueError('Issue cards to a current course term.')
                version = self._require(db, 'response_card_versions', normalized['version_id'])
                template = self._require(db, 'response_card_templates', version['template_id'])
                if template['definition_id'] != term['definition_id']:
                    raise ValueError('The template and term must belong to the same course code.')
                sections = [self._require(db, 'course_sections', sid) for sid in section_ids]
                if any(s['term_id'] != term['id'] or s['number'] not in json.loads(term['sections']) for s in sections):
                    raise ValueError('Every selected section must belong to the selected term.')
                assignment = self._require(db, 'assignments', normalized['assignment_id']) if normalized['assignment_id'] else None
                chart = self._require(db, 'seating_charts', normalized['seating_chart_id']) if normalized['seating_chart_id'] else None
                if chart:
                    chart_sections = {r[0] for r in db.execute('SELECT section_id FROM seating_chart_sections WHERE chart_id=?', (chart['id'],))}
                    if chart['term_id'] != term['id'] or set(section_ids) - chart_sections or (assignment and assignment['id'] != chart['assignment_id']):
                        raise ValueError('The seating chart must match this term, exam, and selected sections.')
                    assignment = self._require(db, 'assignments', chart['assignment_id'])
                if assignment and assignment['definition_id'] != term['definition_id']:
                    raise ValueError('The assignment must belong to the selected course code.')
                if normalized.get('exam_event_id'):
                    self.exam_workflow.issue_context(db, normalized)
                students = self._students(db, term, sections)
                if enrollment_ids and set(enrollment_ids) - {s['enrollment_id'] for s in students}:
                    raise ValueError('Every selected student must belong to the selected sections.')
                if enrollment_ids:
                    students = [s for s in students if s['enrollment_id'] in enrollment_ids]
                students = [s for s in students if not s['dropped']]  # a dropped student is issued no new card
                seats = {}
                room = None
                if chart:
                    room = self._require(db, 'rooms', chart['room_id'])
                    seats = {(r['section_id'], r['enrollment_id']): dict(r) for r in db.execute('SELECT a.section_id,a.enrollment_id,s.* FROM seating_assignments a JOIN room_seats s ON s.id=a.seat_id WHERE a.chart_id=?', (chart['id'],))}
                    students = [s for s in students if (s['section_id'], s['enrollment_id']) in seats]
                if not students or len(students) > 2000:
                    raise ValueError('An issue must have between 1 and 2,000 recipients. Check its rosters and seating audience.')
                issue_id, when, cards = identifier(), timestamp(), []
                schema = json.loads(version['schema_json'])
                for student in students:
                    seat = seats.get((student['section_id'], student['enrollment_id']))
                    variables = self._variables(db, term, student, assignment, chart, seat, room)
                    if normalized.get('exam_event_id'):
                        variables.update(self.exam_workflow.issue_context(db, normalized, student))
                    choices = {}
                    if normalized.get('makeup_offer_id'):
                        # The student's own times on their booking card (ABSENCES "MAKEUP BOOKING"), from the offer recorded first.
                        booking = getattr(self, 'makeup_booking', None)
                        if booking is None:
                            raise ValueError('Makeup booking cards are not available here.')
                        offered = booking.issue_context(db, normalized, student)
                        variables.update(offered['variables'])
                        choices = offered['choices']
                    personalized = personalize(with_choices(schema, choices) if choices else schema, variables)
                    card_id, token = identifier(), secrets.token_urlsafe(32)
                    snapshot = {'id': card_id, 'issue_id': issue_id, 'token_hash': hashlib.sha256(token.encode()).hexdigest(),
                                'schema': personalized, 'policy': policy, 'due_at': due, 'expires_at': expiry}
                    if len(encoded(snapshot).encode()) > 128 * 1024:
                        raise ValueError('A personalized card exceeds the public service size limit.')
                    cards.append((card_id, student, token, snapshot))
                # No publication until every recipient and personalized field validates.
                db.execute('BEGIN IMMEDIATE')
                db.execute('INSERT INTO response_card_issues VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                           (issue_id, version['id'], term['id'], assignment['id'] if assignment else None, chart['id'] if chart else None,
                            encoded(normalized), policy, due, expiry, request_id, request_hash, when))
                for card_id, student, token, snapshot in cards:
                    db.execute('INSERT INTO response_card_recipients(id,issue_id,section_id,enrollment_id,student_name,email,token,snapshot_json,snapshot_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (card_id, issue_id, student['section_id'], student['enrollment_id'], student['name'], student['email'], token, encoded(snapshot), digest(snapshot), when, when))
        return self.get_issue(issue_id)

    def _issue_record(self, db, row, detail=False):
        result = dict(row)
        result.pop('request_hash', None)
        result['context'] = json.loads(result.pop('context_json'))
        result['section_ids'] = result['context']['section_ids']
        version = self._require(db, 'response_card_versions', result['version_id'])
        result['title'] = json.loads(version['schema_json'])['title']
        if detail:
            result['schema'] = json.loads(version['schema_json'])
        result.update(self._issue_labels(db, result))
        counts = Counter()
        recipients = []
        current_time = datetime.now(timezone.utc)
        # Read-only attention counts (UX overhaul, 2026-09-26): replaced cards, their pending remote revocations and
        # submission receipts whose delivery was not confirmed, for the list's attention strip and the issue page's filters.
        invalidations = {row[0]: row[1] for row in db.execute(
            'SELECT x.card_id,x.status FROM response_card_invalidations x JOIN response_card_recipients r ON r.id=x.card_id WHERE r.issue_id=?',
            (result['id'],))}
        counts['revocation_pending'] = sum(status == 'pending' for status in invalidations.values())
        counts['receipt_problem'] = db.execute("""SELECT COUNT(*) FROM response_card_mail m JOIN response_card_recipients r ON r.id=m.card_id
            WHERE r.issue_id=? AND m.kind='confirmation' AND m.status IN ('unconfirmed','failed')
            AND NOT EXISTS(SELECT 1 FROM response_card_invalidations x WHERE x.card_id=r.id)""", (result['id'],)).fetchone()[0]
        counts['delivery_problem'] = counts['queued'] = 0
        for row in db.execute('SELECT r.*,s.number section_number FROM response_card_recipients r JOIN course_sections s ON s.id=r.section_id WHERE issue_id=? ORDER BY s.number,r.student_name,r.id', (result['id'],)):
            card = dict(row)
            token = card.pop('token')
            snapshot = json.loads(card.pop('snapshot_json')) if detail else None
            card.pop('snapshot_hash')
            counts['delivery_problem'] += delivery_problem(card, card['id'] in invalidations)
            counts['queued'] += card['delivery_status'] == 'queued'
            cutoff = result['expires_at'] or (result['due_at'] if result['policy'] == 'until_due' else None)
            if cutoff and datetime.fromisoformat(cutoff.replace('Z', '+00:00')) <= current_time and card['status'] in ('issued', 'opened', 'started'):
                card['status'] = 'expired'
            counts[card['status']] += 1
            counts['total'] += 1
            counts['published'] += card['publish_status'] == 'published'
            counts['emailed'] += card['delivery_status'] == 'sent'
            if detail:
                card['schema'] = snapshot['schema']
                card['link'] = f"{card.pop('published_url')}/r/{token}" if card['published_url'] else None
                card['submissions'] = [unchecked_dropped(json.loads(s[0])) for s in db.execute('SELECT submission_json FROM response_card_submissions WHERE card_id=? ORDER BY submitted_at,id', (card['id'],))]
                card['current_submission'] = next((s for s in card['submissions'] if s['id'] == card['current_submission_id']), None)
                card['mail'] = self.mail.history(db, card['id'])
                # Additive, read-only: the card's replacement (invalidation) state, for the issue page's revocation filter.
                card['revocation'] = invalidations.get(card['id'])
                recipients.append(card)
        result['counts'] = dict(counts)
        if detail:
            result['recipients'] = recipients
        return result

    @staticmethod
    def _issue_labels(db, issue):
        """What an issue's title placeholders and links refer to: its course, term, assignment, chart room and exam record.

        Additive, read-only keys (UX overhaul): the page substitutes {{exam.name}}, {{assignment.name}}, {{course.code}} and
        {{exam.room}} in the issue's title from them; student variables stay literal. No student data.
        """
        term = db.execute('SELECT code,term,term_other,year FROM course_terms WHERE id=?', (issue['term_id'],)).fetchone()
        assignment = db.execute('SELECT title FROM assignments WHERE id=?', (issue['assignment_id'],)).fetchone() if issue.get('assignment_id') else None
        room = db.execute('SELECT r.name FROM seating_charts c JOIN rooms r ON r.id=c.room_id WHERE c.id=?',
                          (issue['seating_chart_id'],)).fetchone() if issue.get('seating_chart_id') else None
        event = None
        if issue.get('assignment_id') and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='exam_events'").fetchone():
            event = db.execute('SELECT id FROM exam_events WHERE term_id=? AND assignment_id=?', (issue['term_id'], issue['assignment_id'])).fetchone()
        return {'course_code': term['code'] if term else '',
                'term_label': f"{term['term_other'] if term['term'] == 'Other' else term['term']} {term['year']}" if term else '',
                'assignment_title': assignment['title'] if assignment else '',
                'room_name': room['name'] if room else '',
                'exam_event_id': event['id'] if event else (issue['context'].get('exam_event_id') or '')}

    def issues(self, term_id=None):
        with connect(self.path) as db:
            # Archived (a view, certified_archive.py): an issue of an assessment whose grades are certified; the page folds it away
            # unless it still needs the instructor.
            archive = certified(db)
            records = [self._issue_record(db, r) for r in db.execute('SELECT * FROM response_card_issues WHERE (? IS NULL OR term_id=?) ORDER BY created_at DESC,id', (term_id, term_id))]
            for record in records:
                record['archived'] = archive_facts(archive.get((record.get('assignment_id'), record['term_id'])), record['created_at']) if record.get('assignment_id') else None
            return records

    def attention(self):
        """Issues of every current term with work for the instructor: answers flagged for review, invitations whose delivery
        failed or was not confirmed on unopened cards, submission receipts not confirmed, and replaced cards whose remote
        revocation is still pending. Read-only; counts and titles only, never a recipient."""
        keys = ('needs_review', 'delivery_problem', 'receipt_problem', 'revocation_pending')
        items = []
        with connect(self.path) as db:
            # A certified assessment's cards no longer need the instructor (certified_archive.py); nothing about them changes.
            for row in db.execute(f'''SELECT i.* FROM response_card_issues i JOIN course_terms t ON t.id=i.term_id
                    WHERE t.is_current=1 AND t.deleted_at IS NULL AND {uncertified(db, "i.assignment_id", "i.term_id", "i.created_at")} ORDER BY i.created_at DESC,i.id'''):
                record = self._issue_record(db, row)
                counts = {key: record['counts'].get(key, 0) for key in keys}
                if counts['needs_review']:  # RESPONSE-CARDS "NO FLAGS AFTER THE EXAM"
                    counts['needs_review'] -= len(exam_over(db, [card[0] for card in db.execute(
                        "SELECT id FROM response_card_recipients WHERE issue_id=? AND status='needs_review'", (row['id'],))]))
                if not any(counts.values()):
                    continue
                items.append({key: record[key] for key in ('id', 'title', 'term_id', 'assignment_id', 'seating_chart_id', 'course_code',
                                                           'term_label', 'assignment_title', 'room_name', 'exam_event_id', 'created_at')}
                             | {'counts': counts})
        return {'items': items, 'total': sum(sum(item['counts'].values()) for item in items)}

    def get_issue(self, key, db=None):
        """Keep historical snapshots readable while blocking stale redistribution.

        A supplied connection belongs to a caller holding the lifecycle lock;
        source verification must happen before a catalog write transaction.
        """
        if db is None:
            with self.rosters.lock(), connect(self.path) as connection:
                return self.get_issue(key, db=connection)
        result = self._issue_record(db, self._require(db, 'response_card_issues', key), True)
        for card in result['recipients']:
            try:
                self.validate_card_access(db, card, verify_source=False)
                if result['context'].get('exam_event_id'):
                    self.mail._validate_exam_context(db, key, card)
            except Exception as error:
                card['link'] = None
                card['distribution_block'] = error.description if isinstance(error, HTTPException) else 'Current exam records could not be verified. Review them before distributing this card.'
        return result

    def validate_card_access(self, db, card, verify_source=True):
        """Caller holds the lifecycle lock; do not redistribute obsolete cards."""
        invalidated = db.execute('SELECT reason FROM response_card_invalidations WHERE card_id=?', (card['id'],)).fetchone()
        if invalidated:
            if invalidated[0] == EXAM_OVER:
                raise Conflict('This card closed at midnight after its exam; its link is disabled or awaiting deactivation.')
            raise Conflict('This card was replaced after a seat change; its earlier link is disabled or awaiting deactivation.')
        if self.seating and hasattr(self.seating, 'validate_card'):
            return self.seating.validate_card(db, card, verify_source=verify_source)
        return False

    def invalidate_cards(self, db, card_ids, reason='seat_changed'):
        """Record permanent invalidation inside the caller's seat-change transaction.

        A publication uses the same lifecycle lock and records its intent first,
        so a pending card has never been public. Failed/interrupted publication
        remains a remote obligation, even when its acknowledgement was lost.
        """
        when = timestamp()
        for card_id in sorted(set(card_ids)):
            card = self._require(db, 'response_card_recipients', card_id)
            state = 'complete' if card['publish_status'] == 'pending' and not card['published_url'] else 'pending'
            db.execute('INSERT OR IGNORE INTO response_card_invalidations(card_id,reason,status,created_at,updated_at) VALUES(?,?,?,?,?)',
                       (card_id, reason, state, when, when))
            db.execute("UPDATE response_card_recipients SET status='revoked',updated_at=? WHERE id=?", (when, card_id))
            # Sent/uncertain envelopes are retained as delivery history. Only a
            # definite non-send may be cancelled, including old-seat receipts.
            db.execute("UPDATE response_card_mail SET status='cancelled',error=?,not_before=0,updated_at=? WHERE card_id=? AND status='pending'",
                       ('The card closed at midnight after its exam.' if reason == EXAM_OVER else 'The card was replaced after a seat change.', when, card_id))
            db.execute("UPDATE response_card_recipients SET delivery_status='cancelled' WHERE id=? AND delivery_status='queued'", (card_id,))

    def close_finished_exams(self, now=None):
        """RESPONSE-CARDS "SEATING CARDS CLOSE AFTER THE EXAM" (requirement, 2026-10-15): from campus midnight after a chart's exam date, every card issued
        from that chart is revoked like a replaced card, its pending mail cancelled, then its public link deactivated. The
        minute timer runs it; a chart with no exam date never closes. A card already revoked (a Revoke before the exam closed it)
        is left revoked. DR NOTICES CLOSE TOO (requirement, 2026-11-01): a DR notice, issued without a chart, closes from campus midnight after its exam day (`exam_over`: the
        later of the DR exam date it states and its assessment's chart dates) the same way. Returns how many cards it closed and what
        deactivation did."""
        today = (now or datetime.now(timezone.utc)).astimezone(CAMPUS_ZONE).date().isoformat()
        with self.rosters.lock(), connect(self.path) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='seating_charts'").fetchone():
                return {'closed': 0, 'complete': 0, 'pending': 0, 'errors': []}
            # Exam dates are stored as YYYY-MM-DD, so text order is date order.
            ids = [row[0] for row in db.execute("""SELECT r.id FROM response_card_recipients r JOIN response_card_issues i ON i.id=r.issue_id
                JOIN seating_charts c ON c.id=i.seating_chart_id WHERE c.exam_date!='' AND c.exam_date<? AND r.status!='revoked'
                AND NOT EXISTS(SELECT 1 FROM response_card_invalidations x WHERE x.card_id=r.id) ORDER BY r.id""", (today,))]
            notices = [card_id for card_id, snapshot_json in db.execute("""SELECT r.id,r.snapshot_json FROM response_card_recipients r
                JOIN response_card_issues i ON i.id=r.issue_id WHERE i.seating_chart_id IS NULL AND r.status!='revoked'
                AND r.snapshot_json LIKE '%confirm_dr_date%' AND NOT EXISTS(SELECT 1 FROM response_card_invalidations x WHERE x.card_id=r.id)
                ORDER BY r.id""") if dr_notice_day(snapshot_json)]
            ids += sorted(exam_over(db, notices, now))
            if ids:
                db.execute('BEGIN IMMEDIATE')
                self.invalidate_cards(db, ids, reason=EXAM_OVER)
                db.commit()
        # Deactivate now rather than one card per Inbox turn; failures keep their back-off and are retried here and by the Inbox.
        return {'closed': len(ids), **self.reconcile_invalidations(limit=2000)}

    def reconcile_invalidations(self, card_ids=None, limit=50):
        """Retry idempotent revocations, without syncing or sending email.

        Local invalidation remains authoritative when the public service is
        unavailable. Explicit callers retry immediately; worker retries back off.
        """
        selected = set(card_ids) if card_ids is not None else None
        if selected == set():
            return {'complete': 0, 'pending': 0, 'errors': []}
        complete, errors = 0, []
        with self.rosters.lock(), connect(self.path) as db:
            rows = [dict(row) for row in db.execute("SELECT r.*,v.not_before FROM response_card_invalidations v JOIN response_card_recipients r ON r.id=v.card_id WHERE v.status='pending' ORDER BY v.created_at,v.card_id")
                    if (selected is None and row['not_before'] <= time.time()) or (selected is not None and row['id'] in selected)][:max(1, min(limit, 2000))]
            for card in rows:
                request_id = 'invalidate-' + card['id']
                try:
                    if not self.remote.config.enabled:
                        raise ServiceUnavailable('Configure the Response Cards public service to deactivate earlier links.')
                    bound = db.execute('SELECT remote_url FROM response_card_sync WHERE id=1').fetchone()[0]
                    if bound and bound != self.remote.config.url:
                        raise Conflict('Response Cards belong to a different public service. Restore that connection before deactivating links.')
                    # A failed PUT may have succeeded remotely. Repeating its
                    # immutable snapshot resolves that uncertainty before revoke.
                    if card['publish_status'] != 'published':
                        self._put_card(card)
                    self._control_card(card['id'], request_id, 'revoke')
                except Exception as error:
                    detail = error.description if isinstance(error, HTTPException) else 'The earlier link could not be deactivated. Faculty will retry.'
                    db.execute("UPDATE response_card_invalidations SET error=?,not_before=?,updated_at=? WHERE card_id=?", (detail[:2000], int(time.time()) + 60, timestamp(), card['id']))
                    db.commit()
                    errors.append({'card_id': card['id'], 'error': detail})
                    continue
                db.execute("UPDATE response_card_invalidations SET status='complete',error='',not_before=0,updated_at=? WHERE card_id=?", (timestamp(), card['id']))
                db.commit()
                complete += 1
            pending = sum(1 for row in db.execute("SELECT card_id FROM response_card_invalidations WHERE status='pending'") if selected is None or row['card_id'] in selected)
        return {'complete': complete, 'pending': pending, 'errors': errors}

    def _put_card(self, card):
        """PUT a card's retained snapshot and require the Worker's publication receipt."""
        result = self.remote.call('PUT', '/faculty-api/cards/' + card['id'], json.loads(card['snapshot_json']))
        if result.get('id', result.get('card_id')) != card['id'] or result.get('published') is not True:
            raise ServiceUnavailable('The public service returned an invalid card publication receipt.')

    def _control_card(self, card_id, request_id, action):
        """POST a control request and require the Worker's control receipt."""
        result = self.remote.call('POST', f'/faculty-api/cards/{card_id}/control', {'request_id': request_id, 'action': action})
        if (result.get('id', result.get('card_id')) != card_id or result.get('applied') is not True
                or result.get('action') != action):
            raise ServiceUnavailable('The public service returned an invalid control receipt.')

    def _bind_remote(self):
        if not self.remote.config.enabled:
            raise ServiceUnavailable('Configure the Response Cards public service before publishing or syncing.')
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM response_card_sync WHERE id=1').fetchone()
            if row['remote_url'] and row['remote_url'] != self.remote.config.url:
                raise Conflict('Response Cards are bound to a different public service. An explicit data migration is required to change it.')
            db.execute('UPDATE response_card_sync SET remote_url=? WHERE id=1', (self.remote.config.url,))

    def publish(self, key):
        self._bind_remote()
        with self.rosters.lock(), connect(self.path) as db:
            self._require(db, 'response_card_issues', key)  # raises NotFound for an unknown issue
            if hasattr(self, 'exam_workflow'):
                self.exam_workflow.validate_publication(key, db)
            current = [dict(r) for r in db.execute("""SELECT r.* FROM response_card_recipients r
                WHERE r.issue_id=? AND r.status!='revoked'
                AND NOT EXISTS (SELECT 1 FROM response_card_invalidations x WHERE x.card_id=r.id) ORDER BY r.id""", (key,))]
            if not current:
                raise Conflict('Every card in this issue was revoked or replaced. Prepare current cards before publishing.')
            cards = [card for card in current if card['publish_status'] != 'published']
            # Validate all before any public side effect. The lifecycle lock
            # spans publication so a concurrent seat change cannot lose a revoke.
            for card in cards:
                self.validate_card_access(db, card)
            for card in cards:
                db.execute("UPDATE response_card_recipients SET publish_status='publishing',updated_at=? WHERE id=?", (timestamp(), card['id']))
                db.commit()  # Retain intent before a possibly uncertain PUT.
                try:
                    self._put_card(card)
                except ServiceUnavailable as error:
                    db.execute("UPDATE response_card_recipients SET publish_status='failed',error=?,updated_at=? WHERE id=? AND publish_status!='published'", (error.description, timestamp(), card['id']))
                    db.commit()
                    raise
                db.execute("UPDATE response_card_recipients SET publish_status='published',published_url=?,error='',updated_at=? WHERE id=?", (self.remote.config.url, timestamp(), card['id']))
                db.commit()
        return self.get_issue(key)

    def sync(self, max_pages=20):
        result = self._sync(max_pages)
        if hasattr(self, 'exam_workflow'):
            added = self.exam_workflow.sync()['added']
            if added:
                result['exam_requests_added'] = added
        if result['events']:
            # Every accepted submission earns the student a confirmation of what they entered.
            queued = self.mail.queue_confirmations()['queued']
            if queued:
                result['confirmations_queued'] = queued
        return result

    def send_invitations(self, key, mail_provider='default'):
        """Queue an emailed link for every published card of an issue that has not been emailed yet."""
        with connect(self.path) as db:
            self._require(db, 'response_card_issues', key)
        queued = self.mail.queue_invitations(key, mail_provider=mail_provider)
        return {**self.get_issue(key), 'queued': queued}

    def link(self, card_id):
        """One card's personal link while it still awaits a response, for the instructor to hand over directly."""
        with self.rosters.lock(), connect(self.path) as db:
            card = self._require(db, 'response_card_recipients', card_id)
            self.validate_card_access(db, card)
            reason = self.mail.awaiting_response(db, card)
            if reason:
                raise Conflict(reason)
            self.mail._validate_exam_context(db, card['issue_id'], card)
            return {'card_id': card_id, 'link': f"{card['published_url']}/r/{card['token']}"}

    def resend(self, card_id, mail_provider='default'):
        """Queue another copy of one pending card's emailed link; the Inbox worker sends it."""
        return self.mail.queue_resend(card_id, mail_provider=mail_provider)

    def _sync(self, max_pages=20):
        self._bind_remote()
        ingested = 0
        for _ in range(max_pages):
            with connect(self.path) as db:
                after = db.execute('SELECT cursor FROM response_card_sync WHERE id=1').fetchone()[0]
            page = self.remote.call('GET', f'/faculty-api/events?after={after}&limit=100')
            if not isinstance(page, dict):
                raise ServiceUnavailable('The public service returned an invalid synchronization page.')
            events = page.get('events')
            cursor = page.get('cursor')
            if (not isinstance(events, list) or len(events) > 100 or type(cursor) is not int or not after <= cursor <= 9007199254740991
                    or type(page.get('has_more')) is not bool):
                raise ServiceUnavailable('The public service returned an invalid synchronization page.')
            with self.rosters.lock(), connect(self.path) as db:
                db.execute('BEGIN IMMEDIATE')
                current = db.execute('SELECT cursor FROM response_card_sync WHERE id=1').fetchone()[0]
                if current != after:
                    continue  # Another reconciler already committed this page.
                previous = after
                for event in events:
                    if not isinstance(event, dict) or type(event.get('cursor')) is not int or not previous < event['cursor'] <= cursor:
                        raise ServiceUnavailable('Response Cards event cursors are invalid; the page was retained remotely for retry.')
                    previous = event['cursor']
                    card_id = event.get('card_id')
                    if not isinstance(card_id, str) or not card_id or len(card_id) > 100:
                        raise ServiceUnavailable('Response Cards event recipient is invalid.')
                    card = db.execute('SELECT * FROM response_card_recipients WHERE id=?', (card_id,)).fetchone()
                    if card is None:
                        raise ServiceUnavailable('The public service returned an event for an unknown Response Card. Synchronization stopped for review.')
                    data = event.get('data')
                    if not isinstance(data, dict) or not isinstance(data.get('status'), str) or data['status'] not in STATUSES:
                        raise ServiceUnavailable('Response Cards event status is invalid.')
                    event_json = encoded(event)
                    if len(event_json.encode()) > 256 * 1024:
                        raise ServiceUnavailable('Response Cards event is too large.')
                    submission = data.get('submission')
                    current_submission = card['current_submission_id']
                    if submission is not None:
                        if (not isinstance(submission, dict) or not isinstance(submission.get('answers'), dict)
                                or not isinstance(submission.get('status'), str) or submission['status'] not in {'submitted', 'verified', 'needs_review'}):
                            raise ServiceUnavailable('Response Cards submission is invalid.')
                        sid = text(submission.get('id'), 'Submission ID', 100)
                        rid = text(submission.get('request_id'), 'Submission request ID', 100)
                        submitted_at = instant(submission.get('submitted_at'), 'Submission time')
                        if not submitted_at:
                            raise ServiceUnavailable('Response Cards submission time is missing.')
                        existing = db.execute('SELECT * FROM response_card_submissions WHERE id=? OR (card_id=? AND request_id=?)', (sid, card['id'], rid)).fetchall()
                        if existing:
                            if len(existing) != 1 or existing[0]['card_id'] != card['id'] or existing[0]['submission_json'] != encoded(submission):
                                raise Conflict('An immutable Response Cards submission changed. Synchronization stopped for review.')
                        else:
                            db.execute('INSERT INTO response_card_submissions VALUES(?,?,?,?,?,?)', (sid, card['id'], rid, encoded(submission), submitted_at, timestamp()))
                        current_submission = sid
                    db.execute('INSERT INTO response_card_events VALUES(?,?,?,?)', (event['cursor'], card['id'], event_json, timestamp()))
                    answered = submission if submission is not None else self._submission(db, current_submission)
                    status = ('revoked' if db.execute('SELECT 1 FROM response_card_invalidations WHERE card_id=?', (card['id'],)).fetchone()
                              else settled_status(data['status'], answered))
                    db.execute('UPDATE response_card_recipients SET status=?,current_submission_id=?,remote_cursor=?,updated_at=? WHERE id=?',
                               (status, current_submission, event['cursor'], timestamp(), card['id']))
                if (events and previous != cursor) or (not events and cursor != after) or (not events and page['has_more']):
                    raise ServiceUnavailable('Response Cards synchronization skipped events; the page was not committed.')
                self._settle_unchecked(db)
                db.execute("UPDATE response_card_sync SET cursor=?,synced_at=?,error='' WHERE id=1", (cursor, timestamp()))
                ingested += len(events)
            if not page['has_more']:
                return {'events': ingested, 'cursor': cursor, 'has_more': False}
        return {'events': ingested, 'cursor': cursor, 'has_more': True}

    @staticmethod
    def _submission(db, submission_id):
        row = db.execute('SELECT submission_json FROM response_card_submissions WHERE id=?', (submission_id,)).fetchone() if submission_id else None
        return json.loads(row[0]) if row else None

    def _settle_unchecked(self, db):
        """Cards flagged before RESPONSE-CARDS "DR LOCATION IS NOT CHECKED" only by an unchecked answer stop needing review;
        the Worker's record and the immutable submissions are unchanged."""
        for row in db.execute("SELECT id,current_submission_id FROM response_card_recipients WHERE status='needs_review'").fetchall():
            status = settled_status('needs_review', self._submission(db, row['current_submission_id']))
            if status != 'needs_review':
                db.execute('UPDATE response_card_recipients SET status=?,updated_at=? WHERE id=?', (status, timestamp(), row['id']))

    def control(self, card_id, data):
        action, request_id = data.get('action'), text(data.get('request_id'), 'Request ID', 100)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id):
            raise ValueError('Control request IDs must contain only letters, digits, underscores, or hyphens.')
        if action not in ('revoke', 'unlock'):
            raise ValueError('Choose revoke or unlock.')
        self._bind_remote()
        with self.rosters.lock(), connect(self.path) as db:
            card = self._require(db, 'response_card_recipients', card_id)
            if action == 'unlock':
                self.validate_card_access(db, card)
            db.execute('BEGIN IMMEDIATE')
            if card['publish_status'] != 'published':
                raise Conflict('Publish this card before changing access.')
            prior = db.execute('SELECT * FROM response_card_controls WHERE request_id=?', (request_id,)).fetchone()
            if prior and (prior['card_id'] != card_id or prior['action'] != action):
                raise Conflict('This control request was already used for another operation.')
            if not prior:
                db.execute('INSERT INTO response_card_controls(request_id,card_id,action,created_at) VALUES(?,?,?,?)', (request_id, card_id, action, timestamp()))
            db.commit()
            self._control_card(card_id, request_id, action)
            db.execute("UPDATE response_card_controls SET status='complete',completed_at=? WHERE request_id=?", (timestamp(), request_id))
        self.sync()
        return self.get_issue(card['issue_id'])


def protect_references(db, previous, replacement=None):
    """Course-wide templates must retain a visible course; issues retain their term."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='response_card_templates'").fetchone():
        return
    has_templates = db.execute('SELECT 1 FROM response_card_templates WHERE definition_id=? LIMIT 1', (previous['definition_id'],)).fetchone()
    if not has_templates:
        return
    definitions = [row[0] for row in db.execute('SELECT definition_id FROM courses WHERE id!=? AND deleted_at IS NULL', (previous['id'],))]
    if replacement:
        definitions.append(replacement['definition_id'])
    if previous['definition_id'] not in definitions:
        raise Conflict('This course has Response Card templates. Keep a visible offering for its course code; archive it by marking it not current.')
