"""Read-only room roster derived from an existing term-owned seating chart.

No new resource owner, retained artifact, or email content is created. Student
UINs come from verified term roster reads; booking schedules come from current
immutable revisions linked to this chart's assignment and selected sections.
"""
from collections import defaultdict
import hashlib
import json
import re
import unicodedata

from werkzeug.exceptions import Conflict

from rosters import connect

BOOKING_FIELDS = ('exam_type', 'date', 'start_time', 'end_time', 'length_minutes', 'campus', 'requested_on')


def _alphabetical(student):
    # Roster names retain their exported order (normally Last, First), without
    # attempting to infer or rewrite multi-part surnames.
    name = ''.join(char for char in unicodedata.normalize('NFKD', student['name']).casefold()
                   if not unicodedata.combining(char))
    return name, student['uin'], student['section_number']


def _response_status(cards, student):
    """Submission state, separate from email delivery and chart warning colors."""
    card = student.get('card') or {}
    if not card.get('card_id'):
        return {'label': 'Updated card not issued' if card.get('state') == 'update_pending' else 'No card issued',
                'no_response': False}
    row = cards.get(card['card_id'])
    if row is None:
        return {'label': 'No card issued', 'no_response': False}
    responded = bool(row['responded']) or row['status'] in ('submitted', 'verified', 'needs_review')
    suffix = {'needs_review': ' — needs review', 'revoked': ' — revoked', 'expired': ' — expired'}.get(row['status'], '')
    return {'label': ('Responded' if responded else 'No response') + suffix, 'no_response': not responded}


def room_roster(seating, chart_id):
    with seating.rosters.lock(), connect(seating.path) as db:
        return roster_snapshot(seating, db, seating.get(chart_id, db))


def roster_snapshot(seating, db, chart):
    """The room roster of a chart the caller read with `seating.get` in `db`, holding the roster lock (the exam-day packet
    reuses it with the same chart read)."""
    seating._require_room_ready(chart['room'])
    if not chart['check']['ok']:
        raise Conflict('Update seating to resolve the roster and seat issues before printing a room roster.')
    card_ids = {(student.get('card') or {}).get('card_id') for student in chart['students']} - {None}
    cards = {}
    if card_ids:
        marks = ','.join('?' for _ in card_ids)
        cards = {row['id']: row for row in db.execute(f'''SELECT r.id,r.status,
                    EXISTS(SELECT 1 FROM response_card_submissions s WHERE s.card_id=r.id) AS responded
                    FROM response_card_recipients r WHERE r.id IN ({marks})''', tuple(card_ids))}
    bookings = defaultdict(list)
    for row in db.execute('''SELECT b.enrollment_id,s.number AS section_number,r.snapshot_json
            FROM dr_exam_bookings b
            JOIN dr_exam_revisions r ON r.id=b.revision_id
            JOIN dr_exam_assignment_links l ON l.booking_id=b.id
            JOIN seating_chart_sections x ON x.section_id=b.section_id
            JOIN course_sections s ON s.id=b.section_id
            JOIN roster_memberships m ON m.section_id=b.section_id AND m.enrollment_id=b.enrollment_id
            WHERE x.chart_id=? AND l.assignment_id=? AND s.term_id=?''',
            (chart['id'], chart['assignment_id'], chart['term_id'])):
        saved = json.loads(row['snapshot_json'])
        bookings[row['enrollment_id']].append({**{key: saved[key] for key in BOOKING_FIELDS},
                                              'section_number': row['section_number']})
    room_students, dr_students, warnings = [], [], []
    for student in chart['students']:
        if not isinstance(student['uin'], str) or not re.fullmatch(r'[0-9]{9}', student['uin']):
            raise Conflict('A student is missing a valid nine-digit UIN. Repair the roster before printing.')
        item = {key: student[key] for key in ('name', 'uin', 'section_number')}
        item['response_card'] = _response_status(cards, student)
        if student['status'] == 'seated':
            room_students.append({**item, 'seat': student['seat']})
        elif student['status'] == 'dr_testing':
            schedules = sorted(bookings[student['enrollment_id']], key=lambda b:
                (b['date'], b['start_time'], b['exam_type'].casefold(), b['section_number']))
            if not schedules:
                raise Conflict('A DR tester has no matching booking in these sections. Review the booking before printing.')
            dr_students.append({**item, 'bookings': schedules})
            if len(schedules) > 1:
                warnings.append(f"{student['name']} has {len(schedules)} bookings linked to this exam; review all listed schedules.")
    room_version = db.execute('SELECT version FROM rooms WHERE id=?', (chart['room_id'],)).fetchone()
    if not room_version or room_version[0] != chart['room']['version']:
        raise Conflict('The room changed. Open the room roster again.')
    snapshot = {key: chart[key] for key in ('id', 'title', 'code', 'term_label', 'sections', 'room_name', 'exam_date', 'exam_time')}
    snapshot.update(room_students=sorted(room_students, key=_alphabetical),
                    dr_students=sorted(dr_students, key=_alphabetical), warnings=warnings)
    snapshot['fingerprint'] = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':'),
                                                       ensure_ascii=False).encode()).hexdigest()
    return snapshot
