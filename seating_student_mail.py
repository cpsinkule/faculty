"""Personal chart email, owned by the term's existing seating chart.

Each envelope references its permanent section/enrollment membership and retains
only its recipient's approved anonymous snapshot and PDF. Catalog snapshots back up envelopes and
receipts. No public chart endpoint or response-card side effect is introduced.
"""
from collections import Counter
import hashlib
import json
import math
import time
import uuid
import zlib

from flask import Blueprint, jsonify, request, send_file
import io
from werkzeug.exceptions import Conflict, NotFound, ServiceUnavailable

from course_storage import timestamp
from document_identity.email_exports import MAX_PDF_BYTES
from response_cards.attachments import PdfMailer
from response_cards.mail import DeliveryError, RELAY_PAUSE, SIGNATURE, paragraphs, valid_address, provider_pauses
from rosters import connect
from seating_mail_merge import _digest
from seating_student_pdf import render_pdf, sanitize_chart

SCHEMA = '''
CREATE TABLE IF NOT EXISTS seating_student_mail (
 id TEXT PRIMARY KEY,
 chart_id TEXT NOT NULL REFERENCES seating_charts(id) ON DELETE CASCADE,
 section_id TEXT NOT NULL, enrollment_id TEXT NOT NULL,
 recipient TEXT NOT NULL, student_name TEXT NOT NULL, seat TEXT NOT NULL,
 content_hash TEXT NOT NULL, subject TEXT NOT NULL, text_body TEXT NOT NULL, html_body TEXT NOT NULL,
 snapshot_blob BLOB NOT NULL, pdf BLOB, output_sha256 TEXT NOT NULL DEFAULT '',
 provider TEXT NOT NULL, gmail_sender TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','accepted','failed','unconfirmed')),
 attempts INTEGER NOT NULL DEFAULT 0, not_before INTEGER NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '', provider_receipt_json TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 FOREIGN KEY(section_id,enrollment_id) REFERENCES roster_memberships(section_id,enrollment_id)
);
CREATE INDEX IF NOT EXISTS seating_student_mail_chart ON seating_student_mail(chart_id,enrollment_id);
CREATE INDEX IF NOT EXISTS seating_student_mail_queue ON seating_student_mail(status,created_at);
'''


class SeatingStudentMail:
    def __init__(self, seating):
        self.seating = seating
        self.rosters, self.path = seating.rosters, seating.path
        with self.rosters.lock(), connect(self.path) as db:
            db.executescript(SCHEMA)
        self.transport = self._deliver

    @property
    def mail(self):
        return self.seating.response_cards.mail

    def _deliver(self, job, pdf):
        return PdfMailer(self.mail).deliver(job, pdf)

    def _context(self, db, chart_id):
        chart = self.seating.get(chart_id, db)
        self.seating._require_room_ready(chart['room'])
        if not chart['check']['ok']:
            raise Conflict('Update seating and resolve its roster or room issues before emailing student charts.')
        term = dict(db.execute('SELECT * FROM course_terms WHERE id=?', (chart['term_id'],)).fetchone())
        live = {(s['section_id'], s['enrollment_id']): s for s in
                self.seating.response_cards._students(db, term, chart['section_details'])}
        return chart, live

    @staticmethod
    def _content(chart, student, email):
        snapshot = sanitize_chart(chart, student['enrollment_id'])
        # Dates belong to this chart and affect the approved message even when
        # absent from the physical room rendering.
        return snapshot, _digest({'snapshot': snapshot, 'email': email,
                                  'exam_date': chart.get('exam_date'), 'exam_time': chart.get('exam_time')})

    def _plan(self, db, chart_id, audience, mail_provider):
        if audience not in ('unsent', 'all'):
            raise ValueError('Choose Not emailed this chart or Send again.')
        if mail_provider not in ('default', 'gmail'):
            raise ValueError('Choose default or gmail for mail_provider.')
        chart, live = self._context(db, chart_id)
        mail = self.mail.status()
        try:
            provider = self.mail.resolve_provider(mail_provider)
        except ServiceUnavailable:
            provider = None
        sender = mail['gmail'].get('email') if provider == 'gmail' else 'Faculty PDF email service' if provider else ''
        gmail_sender = sender if provider == 'gmail' else ''
        jobs = [dict(row) for row in db.execute('''SELECT id,enrollment_id,section_id,recipient,student_name,seat,
                    content_hash,status,error,created_at FROM seating_student_mail WHERE chart_id=? ORDER BY created_at DESC,rowid DESC''', (chart_id,))]
        addresses = Counter(s['email'].casefold() for s in live.values() if valid_address(s['email']))
        entries, skipped = [], []
        for student in chart['students']:
            record = live.get((student['section_id'], student['enrollment_id']))
            email = record['email'] if record else ''
            recipient = {key: student[key] for key in ('enrollment_id', 'section_id', 'name', 'section_number', 'seat')}
            recipient['email'] = email
            reason = ''
            if student['status'] != 'seated':
                reason = 'No classroom seat assigned.'
            elif not record or record['name'] != student['name']:
                reason = 'The current roster name could not be verified.'
            elif not valid_address(email):
                reason = 'No valid roster email address.'
            elif addresses[email.casefold()] > 1:
                # One enrollment may appear in two sections. It is seated once;
                # duplicate addresses are unsafe only across different people.
                owners = {s['enrollment_id'] for s in live.values() if s['email'].casefold() == email.casefold()}
                if len(owners) > 1:
                    reason = 'This email address is shared by multiple students. Correct the roster before sending.'
            snapshot, content_hash = (None, '') if reason else self._content(chart, student, email)
            earlier = [job for job in jobs if job['enrollment_id'] == student['enrollment_id']]
            if not reason and any(job['status'] in ('pending', 'sending') for job in earlier):
                reason = 'A student chart email is already queued or sending.'
            if not reason and any(job['status'] == 'unconfirmed' and job['content_hash'] == content_hash for job in earlier):
                reason = 'Earlier delivery of this chart is unconfirmed; it will not be resent automatically.'
            if not reason and audience == 'unsent' and any(job['status'] == 'accepted' and job['content_hash'] == content_hash for job in earlier):
                reason = 'Already emailed this chart; choose Send again to send another copy.'
            if reason:
                skipped.append({**recipient, 'reason': reason})
            else:
                entries.append({'recipient': recipient, 'snapshot': snapshot, 'content_hash': content_hash})
        subject = ' '.join(f"{chart['code']} · {chart['title']} · Your seating chart".split())[:300]
        plan = {'chart_id': chart_id, 'version': chart['version'], 'title': chart['title'], 'code': chart['code'],
                'room_name': chart['room_name'], 'audience': audience, 'mail_provider': mail_provider,
                'mail': mail, 'sender': sender, 'can_send': bool(provider and entries), 'eligible_count': len(entries),
                'skipped_count': len(skipped), 'recipients': [entry['recipient'] for entry in entries], 'skipped': skipped,
                'subject': subject, 'history': [{'name': job['student_name'], 'email': job['recipient'],
                    **{key: job[key] for key in ('seat', 'status', 'error', 'created_at')}} for job in jobs[:200]]}
        plan['fingerprint'] = _digest({'chart_id': chart_id, 'version': chart['version'], 'room_version': chart['room']['version'],
            'audience': audience, 'mail_provider': mail_provider, 'provider': provider, 'gmail_sender': gmail_sender,
            'subject': subject, 'recipients': plan['recipients'], 'contents': [entry['content_hash'] for entry in entries], 'skipped': skipped})
        return plan, entries, chart, provider, gmail_sender

    def preview(self, chart_id, audience='unsent', mail_provider='default'):
        with self.rosters.lock(), connect(self.path) as db:
            return self._plan(db, chart_id, audience, mail_provider)[0]

    def pdf(self, chart_id, enrollment_id, fingerprint, audience='unsent', mail_provider='default'):
        with self.rosters.lock(), connect(self.path) as db:
            plan, entries, _, _, _ = self._plan(db, chart_id, audience, mail_provider)
            if fingerprint != plan['fingerprint']:
                raise Conflict('The chart or recipients changed. Refresh the email preview.')
            entry = next((item for item in entries if item['recipient']['enrollment_id'] == enrollment_id), None)
            if not entry:
                raise NotFound('Choose a student eligible for this preview.')
            return render_pdf(entry['snapshot'])

    def send(self, chart_id, data):
        try:
            request_id = str(uuid.UUID(data.get('request_id', '')))
        except (ValueError, TypeError, AttributeError):
            raise ValueError('A unique request ID is required.') from None
        fingerprint = data.get('fingerprint')
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            raise ValueError('Refresh the email preview before sending.')
        audience, mail_provider = data.get('audience', 'unsent'), data.get('mail_provider', 'default')
        command_hash = _digest({'chart_id': chart_id, 'fingerprint': fingerprint, 'audience': audience,
                                'mail_provider': mail_provider, 'request_id': request_id})
        with self.rosters.lock(), connect(self.path) as db:
            for event in db.execute("SELECT detail_json FROM seating_events WHERE chart_id=? AND kind='student_chart_email'", (chart_id,)):
                previous = json.loads(event[0])
                if previous.get('request_id') == request_id:
                    if previous['command_hash'] != command_hash:
                        raise Conflict('This request ID already belongs to a different email command.')
                    return previous['receipt']
            plan, entries, chart, provider, gmail_sender = self._plan(db, chart_id, audience, mail_provider)
            if fingerprint != plan['fingerprint']:
                raise Conflict('The chart, recipients or email account changed. Refresh the email preview.')
            if not provider:
                raise ServiceUnavailable('The selected email service is unavailable.')
            if not entries:
                raise Conflict('No students are eligible. Review the skipped students.')
            prepared = []
            for entry in entries:
                snapshot_blob = zlib.compress(json.dumps(entry['snapshot'], sort_keys=True, separators=(',', ':')).encode())
                recipient = entry['recipient']
                text = (f"Hello,\n\nYour seating chart for {chart['code']} · {chart['title']} is attached. "
                        f"Your seat is {recipient['seat']} in {chart['room_name']}.\n\n"
                        'Your seat is highlighted and marked Your seat, with its seat number underneath. '
                        'Other assigned seats are shaded and marked Occupied, with their seat numbers underneath. '
                        'This copy reflects the seating plan when it was sent.\n\n' + SIGNATURE)
                prepared.append((str(uuid.uuid4()), chart_id, recipient['section_id'], recipient['enrollment_id'],
                    recipient['email'], recipient['name'], recipient['seat'], entry['content_hash'], plan['subject'],
                    text, paragraphs(text), snapshot_blob, provider, gmail_sender))
            db.execute('BEGIN IMMEDIATE')
            room = db.execute('SELECT version FROM rooms WHERE id=?', (chart['room_id'],)).fetchone()
            if not room or room[0] != chart['room']['version']:
                raise Conflict('The room changed. Refresh the email preview.')
            now = timestamp()
            db.executemany('''INSERT INTO seating_student_mail(id,chart_id,section_id,enrollment_id,recipient,student_name,seat,
                content_hash,subject,text_body,html_body,snapshot_blob,provider,gmail_sender,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', [(*row, now, now) for row in prepared])
            receipt = {'chart_id': chart_id, 'request_id': request_id, 'queued': len(prepared), 'skipped_count': len(plan['skipped'])}
            self.seating.record_event(db, chart_id, 'student_chart_email',
                                      {'request_id': request_id, 'command_hash': command_hash, 'receipt': receipt}, now)
            return receipt

    @staticmethod
    def _finish(db, job, status, error=''):
        db.execute('UPDATE seating_student_mail SET status=?,error=?,updated_at=? WHERE id=?',
                   (status, error[:2000], timestamp(), job['id']))

    def process_one(self):
        with self.rosters.lock(), connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            interrupted = list(db.execute("SELECT id,provider_receipt_json FROM seating_student_mail WHERE status='sending'"))
            for job in interrupted:
                self._finish(db, job, 'accepted' if job['provider_receipt_json'] else 'unconfirmed',
                             '' if job['provider_receipt_json'] else 'Sending was interrupted; delivery is unconfirmed.')
            pauses = provider_pauses(db)
            ready = [provider for provider in ('gmail', 'resend', 'relay')
                     if time.time() >= pauses.get('relay' if provider in ('resend', 'relay') else provider, 0)]
            row = db.execute('SELECT * FROM seating_student_mail WHERE status=\'pending\' AND provider IN (' +
                ','.join('?' for _ in ready) + ') ORDER BY created_at,id LIMIT 1', ready).fetchone() if ready else None
            if row is None:
                return bool(interrupted)
            job = dict(row)
            # Preparing a PDF is safe to repeat after a restart. Retain pending
            # until the attachment is durable and transport is about to begin.
            db.commit()
            try:
                chart, live = self._context(db, job['chart_id'])
                student = next((s for s in chart['students'] if s['enrollment_id'] == job['enrollment_id']
                                and s['section_id'] == job['section_id'] and s['status'] == 'seated'), None)
                record = live.get((job['section_id'], job['enrollment_id']))
                if not student or not record or record['name'] != student['name'] or record['email'] != job['recipient']:
                    raise ValueError('The recipient or assigned seat changed.')
                if any(s['email'].casefold() == job['recipient'].casefold() and s['enrollment_id'] != job['enrollment_id'] for s in live.values()):
                    raise ValueError('Multiple students now share this email address.')
                snapshot, content_hash = self._content(chart, student, job['recipient'])
                if content_hash != job['content_hash']:
                    raise ValueError('The seating chart changed after this email was queued.')
                retained = json.loads(zlib.decompress(job['snapshot_blob']))
                if retained != snapshot:
                    raise ValueError('The approved chart snapshot failed verification.')
                if job['pdf'] is None:
                    job['pdf'] = render_pdf(retained)
                    if not job['pdf'].startswith(b'%PDF-') or len(job['pdf']) > MAX_PDF_BYTES:
                        raise ValueError('The chart PDF is invalid or too large to email.')
                    job['output_sha256'] = hashlib.sha256(job['pdf']).hexdigest()
                    db.execute('UPDATE seating_student_mail SET pdf=?,output_sha256=? WHERE id=?',
                               (job['pdf'], job['output_sha256'], job['id']))
                    db.commit()
                elif hashlib.sha256(job['pdf']).hexdigest() != job['output_sha256']:
                    raise ValueError('The retained chart PDF failed verification.')
                db.execute('BEGIN IMMEDIATE')
                room = db.execute('SELECT version FROM rooms WHERE id=?', (chart['room_id'],)).fetchone()
                if not room or room[0] != chart['room']['version']:
                    raise ValueError('The room changed before delivery.')
            except Exception:
                self._finish(db, job, 'failed', 'This chart or recipient changed, or its PDF could not be prepared or verified. Refresh the chart email preview before sending again.')
                return True
            db.execute("UPDATE seating_student_mail SET status='sending',attempts=attempts+1,not_before=0,updated_at=? WHERE id=?",
                       (timestamp(), job['id']))
            db.commit()  # Durable send intent precedes any provider call.
            db.execute('BEGIN IMMEDIATE')
            room = db.execute('SELECT version FROM rooms WHERE id=?', (chart['room_id'],)).fetchone()
            if not room or room[0] != chart['room']['version']:
                self._finish(db, job, 'failed', 'The room changed before delivery. Refresh the chart email preview.')
                return True
            try:
                self.mail._check_delivery_provider(job['provider'], job['gmail_sender'])
                receipt = self.transport(job, job['pdf'])
            except DeliveryError as error:
                if error.unavailable:
                    db.execute("UPDATE seating_student_mail SET status='pending',error=?,not_before=?,updated_at=? WHERE id=?",
                               (str(error)[:2000], math.ceil(time.time()) + (error.retry_after or RELAY_PAUSE), timestamp(), job['id']))
                    return False
                self._finish(db, job, 'unconfirmed' if error.uncertain else 'failed', str(error))
            except Exception:
                self._finish(db, job, 'unconfirmed', 'Delivery could not be confirmed; this email will not be retried automatically.')
            else:
                db.execute('UPDATE seating_student_mail SET provider_receipt_json=?,updated_at=? WHERE id=?',
                           (json.dumps(receipt), timestamp(), job['id']))
                db.commit()
                self._finish(db, job, 'accepted')
        return True


def register(app, service):
    api = Blueprint('seating_student_mail', __name__, url_prefix='/api/seating')

    @api.get('/<chart_id>/student-email')
    def preview(chart_id):
        return jsonify(service.preview(chart_id, request.args.get('audience', 'unsent'), request.args.get('mail_provider', 'default')))

    @api.get('/<chart_id>/student-email/preview')
    def pdf(chart_id):
        content = service.pdf(chart_id, request.args.get('enrollment_id'), request.args.get('fingerprint'),
                              request.args.get('audience', 'unsent'), request.args.get('mail_provider', 'default'))
        return send_file(io.BytesIO(content), mimetype='application/pdf', download_name='seating-chart.pdf', max_age=0)

    @api.post('/<chart_id>/student-email')
    def send(chart_id):
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError('The request must be a JSON object.')
        return jsonify(service.send(chart_id, data))

    app.register_blueprint(api)
