"""Explicit Gmail invitations for the existing cards of a confirmed seating chart.

The term-owned chart retains the request receipt in seating_events. Delivery
envelopes remain owned by their existing response cards; no new resource owner
or published card is created, and provider calls belong only to the mail worker.
"""
import json

from werkzeug.exceptions import Conflict

from course_storage import timestamp
from response_cards.mail import SIGNATURE
from rosters import connect
from seating_mail_merge import SeatingMailMerge, _digest


class SeatingGmail:
    def __init__(self, seating):
        self.seating = seating
        self.audience = SeatingMailMerge(seating)
        self.cards = seating.response_cards

    def _plan(self, db, chart_id, audience):
        shared, entries, room = self.audience._audience(db, chart_id, audience)
        gmail = self.cards.mail.gmail.status()
        sender = gmail.get('email') or ''
        configured = self.cards.mail.config.provider in ('auto', 'gmail', 'relay', 'resend')
        plan = {key: shared[key] for key in ('chart_id', 'version', 'title', 'code', 'room_name', 'audience',
                                             'subject', 'eligible_count', 'skipped_count', 'skipped')}
        plan.update(sender=sender, sender_name=SIGNATURE, gmail=gmail,
                    can_send=bool(gmail.get('connected') and configured and entries),
                    recipients=[{**recipient, 'subject': entry['message']['subject']}
                                for recipient, entry in zip(shared['recipients'], entries)])
        plan['fingerprint'] = _digest({'preview': plan, 'audience': shared['fingerprint'], 'room': room,
                                       'messages': [entry['message'] for entry in entries]})
        return plan, entries, room

    def preview(self, chart_id, audience='unsent'):
        with self.seating.rosters.lock(), connect(self.seating.path) as db:
            plan, _, _ = self._plan(db, chart_id, audience)
            return plan

    def send(self, chart_id, data):
        request_id = data.get('request_id')
        if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 120:
            raise ValueError('A request_id of 1 to 120 characters is required.')
        request_id = request_id.strip()
        audience = data.get('audience', 'unsent')
        if audience not in ('unsent', 'pending'):
            raise ValueError('Choose Not emailed yet or All awaiting response.')
        fingerprint = data.get('fingerprint')
        if not isinstance(fingerprint, str) or len(fingerprint) != 64 or any(char not in '0123456789abcdef' for char in fingerprint):
            raise ValueError('Refresh the Gmail preview before sending.')
        command_hash = _digest({'chart_id': chart_id, 'audience': audience, 'fingerprint': fingerprint,
                                'provider': 'gmail', 'request_id': request_id})
        with self.seating.rosters.lock(), connect(self.seating.path) as db:
            # The roster lifecycle lock serializes every send request and the
            # worker. Check receipts before a fresh audience check: after a
            # successful first request, those same cards are now queued or sent.
            for event in db.execute("SELECT detail_json FROM seating_events WHERE chart_id=? AND kind='gmail_send' ORDER BY id DESC", (chart_id,)):
                previous = json.loads(event['detail_json'])
                if previous.get('request_id') != request_id:
                    continue
                if previous.get('command_hash') != command_hash:
                    raise Conflict('This request_id was already used for a different Gmail send. Refresh the preview.')
                return previous['receipt']
            plan, entries, room = self._plan(db, chart_id, audience)
            self.cards.mail.resolve_provider('gmail')
            if fingerprint != plan['fingerprint']:
                raise Conflict('The Gmail account, recipients or response cards changed. Refresh the Gmail preview before sending.')
            if not entries:
                raise Conflict('No response-card invitations are eligible. Review the skipped students.')
            # Verified roster reads and any required storage restoration have
            # finished before opening the catalog write transaction.
            db.execute('BEGIN IMMEDIATE')
            current_room = db.execute('SELECT version FROM rooms WHERE id=?', (room['id'],)).fetchone()
            if current_room is None or current_room['version'] != room['version']:
                raise Conflict('The room changed. Refresh the Gmail preview before sending.')
            now = timestamp()
            for entry in entries:
                if self.cards.mail.gmail.email.casefold() != plan['sender'].casefold():
                    raise Conflict('The Gmail account changed. Refresh the Gmail preview before sending.')
                self.cards.mail._queue(db, entry['card'], 'invitation', '', entry['message'], now,
                                       provider='gmail', gmail_sender=plan['sender'])
                db.execute("UPDATE response_card_recipients SET delivery_status='queued',updated_at=? WHERE id=?", (now, entry['card_id']))
            receipt = {'chart_id': chart_id, 'request_id': request_id, 'audience': audience, 'sender': plan['sender'],
                       'queued': len(entries), 'skipped_count': plan['skipped_count']}
            detail = {'request_id': request_id, 'command_hash': command_hash, 'receipt': receipt}
            self.seating.record_event(db, chart_id, 'gmail_send', detail, now, sort_keys=True)
            return receipt
