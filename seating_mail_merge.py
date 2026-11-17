"""Ephemeral Gmail spreadsheets derived from a term-owned seating chart.

No new owner, stored payload, publication, queue entry or delivery receipt is
created. The instructor explicitly downloads personal links to existing cards.
"""
from collections import Counter
import hashlib
import io
import json
from urllib.parse import urlsplit

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from werkzeug.exceptions import Conflict

from canonical_json import digest as _digest
from response_cards.mail import valid_address
from response_cards.schema import SchemaError, personalize
from rosters import connect


HEADERS = ('Email', 'FirstName', 'Course', 'Section', 'Exam', 'ResponseCardLink', 'Message')
GMAIL_HELP = 'https://support.google.com/mail/answer/12921167?hl=en'


def _comparable(schema):
    """The salted UIN digest varies on rendering; all other frozen text must match."""
    result = json.loads(json.dumps(schema))
    for field in result.get('fields', []):
        verification = field.get('verification') or {}
        if verification.get('mode') == 'uin-digest':
            verification['expected'] = '[same retained student identity]'
    return result


class SeatingMailMerge:
    def __init__(self, seating):
        self.seating = seating
        self.cards = seating.response_cards

    def _candidate(self, db, chart, student, card, audience, term, assignment, live):
        if not card:
            return 'No current response card. Prepare cards for this student first.'
        if card['term_id'] != chart['term_id'] or card['assignment_id'] != chart['assignment_id']:
            return 'The response card belongs to another course term or exam.'
        kind = 'dr' if student['status'] == 'dr_testing' else 'seat'
        if kind == 'seat' and card['seating_chart_id'] != chart['id']:
            return 'The response card belongs to another seating chart.'
        if kind == 'dr' and card['seating_chart_id']:
            return 'The response card does not match this student’s testing arrangement.'
        if not live or live['name'] != card['student_name'] or live['email'] != card['email']:
            return 'Roster name or email changed. Revoke the old card in Response Cards, then reconfirm the chart.'
        if not valid_address(card['email']):
            return 'No valid email address on the response card.'
        reason = self.cards.mail.awaiting_response(db, card)
        if reason:
            return reason
        address = urlsplit(card['published_url'])
        if address.scheme != 'https' or not address.netloc or address.username or address.password or address.query or address.fragment or not card['token']:
            return 'The published card has no valid personal link.'
        jobs = list(db.execute("SELECT status FROM response_card_mail WHERE card_id=? AND kind='invitation'", (card['id'],)))
        statuses = {row['status'] for row in jobs}
        if statuses & {'pending', 'sending'} or card['delivery_status'] == 'queued':
            return 'An invitation is already queued or being sent by Faculty.'
        if audience == 'unsent':
            if 'unconfirmed' in statuses or card['delivery_status'] == 'unconfirmed':
                return 'Earlier email delivery is unconfirmed. Check it before choosing All awaiting response.'
            if 'accepted' in statuses or card['delivery_status'] == 'sent':
                return 'Already emailed by Faculty; choose All awaiting response to send a reminder.'
        seat = None
        if kind == 'seat':
            if student['status'] != 'seated' or not student['seat_id']:
                return 'This student has no usable classroom seat. Update seating first.'
            seat = db.execute('SELECT * FROM room_seats WHERE id=? AND room_id=?', (student['seat_id'], chart['room_id'])).fetchone()
            recorded = ((chart.get('cards') or {}).get('seat') or {}).get('seats', {}).get(student['enrollment_id'])
            if not seat or seat['broken'] or seat['blocked'] or recorded != seat['label']:
                return 'The seat changed after this card was prepared. Prepare a replacement card.'
        else:
            schedules = db.execute('''SELECT b.id FROM dr_exam_bookings b JOIN dr_exam_assignment_links l ON l.booking_id=b.id
                WHERE b.section_id=? AND b.enrollment_id=? AND l.assignment_id=?''',
                (student['section_id'], student['enrollment_id'], chart['assignment_id'])).fetchall()
            if len(schedules) != 1 or student['seat_id']:
                return 'Review this student’s testing arrangement before exporting the card.'
        snapshot = json.loads(card['snapshot_json'])
        if hashlib.sha256(card['token'].encode()).hexdigest() != snapshot.get('token_hash'):
            return 'The response card failed its retained link check.'
        try:
            variables = self.cards._variables(db, term, live, assignment, chart if kind == 'seat' else None,
                                              dict(seat) if seat else None, chart['room'] if seat else None)
            current = personalize(json.loads(card['schema_json']), variables)
            if _comparable(current) != _comparable(snapshot['schema']):
                return 'The exam, room, seat or booking details changed. Revoke the old card in Response Cards, then reconfirm the chart.'
            self.cards.mail._validate_exam_context(db, card['issue_id'], card)
        except (ValueError, Conflict, SchemaError):
            return 'The card’s current exam details cannot be verified. Review the details, revoke the old card in Response Cards, then reconfirm the chart.'
        return ''

    def _audience(self, db, chart_id, audience):
        """Shared current-card audience; private entries never leave the server."""
        if audience not in ('unsent', 'pending'):
            raise ValueError('Choose Not emailed yet or All awaiting response.')
        if self.cards is None:
            raise Conflict('Response Cards are unavailable.')
        chart = self.seating.get(chart_id, db)
        if not chart['confirmed_at']:
            raise Conflict('Confirm the seating chart before preparing email invitations.')
        self.seating._require_room_ready(chart['room'])
        term = dict(db.execute('SELECT * FROM course_terms WHERE id=?', (chart['term_id'],)).fetchone())
        assignment = dict(db.execute('SELECT * FROM assignments WHERE id=?', (chart['assignment_id'],)).fetchone())
        live = {(s['section_id'], s['enrollment_id']): s for s in self.cards._students(db, term, chart['section_details'])}
        latest, replacements = {}, set()
        for kind in ('seat', 'dr'):
            issue_ids = self.seating._issue_ids((chart.get('cards') or {}).get(kind))
            if not issue_ids:
                continue
            for row in db.execute(f'''SELECT r.*,i.term_id,i.assignment_id,i.seating_chart_id,v.schema_json
                FROM response_card_recipients r JOIN response_card_issues i ON i.id=r.issue_id
                JOIN response_card_versions v ON v.id=i.version_id
                WHERE r.issue_id IN ({','.join('?' for _ in issue_ids)}) ORDER BY r.created_at,r.rowid''', issue_ids):
                key = (kind, row['section_id'], row['enrollment_id'])
                earlier = latest.get(key)
                if earlier and earlier['status'] == 'revoked':
                    replacements.add(row['id'])
                latest[key] = dict(row)
        entries, skipped = [], []
        for student in chart['students']:
            kind = 'dr' if student['status'] == 'dr_testing' else 'seat'
            card = latest.get((kind, student['section_id'], student['enrollment_id']))
            person = live.get((student['section_id'], student['enrollment_id']))
            reason = self._candidate(db, chart, student, card, audience, term, assignment, person)
            if reason:
                skipped.append({'name': student['name'], 'section_number': student['section_number'], 'reason': reason})
                continue
            # Use the initial invitation for this card, regardless of delivery
            # history. Gmail's required name mapping must not disclose a name.
            invitation = self.cards.mail.invitation(db, card, replacement=card['id'] in replacements)
            link = f"{card['published_url']}/r/{card['token']}"
            row = [card['email'], 'Student', chart['code'], str(student['section_number']), chart['title'], link, invitation['text']]
            if any(ILLEGAL_CHARACTERS_RE.search(value) or len(value) > 32767 for value in row):
                skipped.append({'name': student['name'], 'section_number': student['section_number'],
                                'reason': 'The invitation contains text that cannot be represented safely in a spreadsheet.'})
                continue
            entries.append({'name': student['name'], 'email': card['email'], 'section_number': student['section_number'],
                            'kind': kind, 'delivery': card['delivery_status'], 'card_id': card['id'], 'row': row,
                            'card': card, 'message': invitation})
        duplicates = Counter(entry['email'].casefold() for entry in entries)
        unique = []
        for entry in entries:
            if duplicates[entry['email'].casefold()] > 1:
                skipped.append({'name': entry['name'], 'section_number': entry['section_number'],
                                'reason': 'More than one current card uses this email. Resolve the duplicate address before mail merge.'})
            else:
                unique.append(entry)
        subject = f"IMPORTANT: Information about your upcoming {chart['title']} for {chart['code']}"[:300]
        plan = {'chart_id': chart_id, 'version': chart['version'], 'title': chart['title'], 'code': chart['code'],
                'room_name': chart['room_name'], 'audience': audience, 'subject': subject, 'body': '@Message',
                'filename': f'seating-mail-merge-{chart_id}.xlsx', 'eligible_count': len(unique), 'skipped_count': len(skipped),
                'recipients': [{k: entry[k] for k in ('name', 'email', 'section_number', 'kind', 'delivery')} for entry in unique],
                'skipped': skipped}
        # Keep the workbook fingerprint and output unchanged. Direct delivery
        # also needs the validated card and exact original invitation internally.
        plan['fingerprint'] = _digest({'plan': plan, 'rows': [{key: value for key, value in entry.items()
                                                            if key not in ('card', 'message')} for entry in unique]})
        return plan, unique, {'id': chart['room']['id'], 'version': chart['room']['version']}

    def _plan(self, db, chart_id, audience):
        plan, entries, _ = self._audience(db, chart_id, audience)
        return plan, [entry['row'] for entry in entries]

    def preview(self, chart_id, audience='unsent'):
        with self.seating.rosters.lock(), connect(self.seating.path) as db:
            plan, _ = self._plan(db, chart_id, audience)
            return plan

    def download(self, chart_id, audience, fingerprint):
        with self.seating.rosters.lock(), connect(self.seating.path) as db:
            plan, rows = self._plan(db, chart_id, audience)
            if not isinstance(fingerprint, str) or fingerprint != plan['fingerprint']:
                raise Conflict('The recipients or response cards changed. Refresh the mail merge preview before downloading.')
            if not rows:
                raise Conflict('No response-card invitations are eligible for this export. Review the skipped students.')
        return workbook(plan, rows), plan['filename']


def workbook(plan, rows):
    """Everything in Gmail's first tab is text, including formula-like names."""
    book = Workbook()
    recipients = book.active
    recipients.title = 'Recipients'
    sheets = [(recipients, [HEADERS, *rows])]
    instructions = [
        ('Step', 'Instructions'),
        ('1', 'Import this XLSX into Google Sheets. Keep Recipients as the first tab. Keep the sheet private; its links are personal.'),
        ('2', 'In Gmail on a computer, Compose, turn on Mail merge beside To, then Add from a spreadsheet.'),
        ('3', 'Choose the imported sheet. Map Email to email and FirstName to first name. FirstName contains only the generic word Student; do not add student names.'),
        ('Subject', plan['subject']),
        ('Body', 'Type @ in Gmail and select the Message merge tag. Message contains the initial invitation and personal card link, with a generic greeting and no student name.'),
        ('4', 'Review Gmail’s preview before sending. Gmail needs an eligible Workspace plan. Merge tags do not work in subject lines or hyperlinked text.'),
        ('Delivery', 'Downloading does not send email or mark any student as emailed in Faculty. Gmail delivery is not tracked by Faculty; response-card completion still syncs normally.'),
        ('Freshness', 'Export again after changes to seats, schedules, roster addresses or cards. Faculty cannot update a spreadsheet already downloaded.'),
        ('Audience', 'Not emailed yet' if plan['audience'] == 'unsent' else 'All awaiting response, including previously emailed or unconfirmed delivery'),
        ('Help', GMAIL_HELP),
    ]
    sheets.append((book.create_sheet('Instructions'), instructions))
    if plan['skipped']:
        sheets.append((book.create_sheet('Skipped'), [('Name', 'Section', 'Reason'), *[(s['name'], str(s['section_number']), s['reason']) for s in plan['skipped']]]))
    for sheet, values in sheets:
        for row in values:
            # Display-only notes can describe rejected roster text. Clean them so
            # one invalid name cannot prevent everyone else's export. Recipient
            # values, particularly addresses and personal links, stay exact.
            cells = list(row) if sheet is recipients else [ILLEGAL_CHARACTERS_RE.sub('\ufffd', str(value))[:32767] for value in row]
            sheet.append(cells)
            for cell in sheet[sheet.max_row]:
                cell.data_type = 's'
                cell.number_format = '@'
                cell.alignment = Alignment(vertical='top', wrap_text=True)
        sheet.freeze_panes = 'A2'
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='365F48')
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = 24
    recipients.column_dimensions['A'].width = 34
    recipients.column_dimensions['F'].width = 50
    recipients.column_dimensions['G'].width = 90
    for row in range(2, recipients.max_row + 1):
        recipients.row_dimensions[row].height = 45
    book['Instructions'].column_dimensions['B'].width = 110
    if 'Skipped' in book:
        book['Skipped'].column_dimensions['C'].width = 90
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()
