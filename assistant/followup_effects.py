"""FOLLOW-UP CLARITY (requirement, 2026-10-03): what one
waiting follow-up card would do, said plainly, from the card's exact request and the records it names as they are now.

A read projection beside each waiting card (ASSISTANT-FOLLOWUP-SUGGESTIONS): it writes nothing and never changes what Approve
submits; the card keeps its exact request one click away. Labels come from the owning services where they have them (absence
reasons and decisions); the follow-up states read as the Follow-up page names them.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from decimal import Decimal, InvalidOperation

from absences.service import NOTICES, REASONS, STATUSES, dates_text, notice_timing
from scheduling.clock import CAMPUS_ZONE, day_label, display_clock

# The Follow-up page's own words for a follow-up's or appointment's state (student-support.js SUPPORT_STATES).
STATES = {'needs_reply': 'Needs my reply', 'todo': 'To do', 'waiting': 'Waiting on student', 'snoozed': 'Follow up later', 'resolved': 'Resolved',
          'scheduled': 'Scheduled', 'attended': 'Attended', 'cancelled': 'Cancelled', 'no_show': 'No-show'}
EXPECTATIONS = {'expected': 'Takes the exam', 'absent': 'Absent', 'excused': 'Excused from the exam'}
# Bookkeeping a person never reads: request identities, revision checks, captured-message pins and record IDs.
INTERNAL = {'request_id', 'base_revision', 'observed', 'source_key', 'version', 'seen', 'fingerprint', 'confirmation', 'kind'}
PAGES = (('/api/absences', 'Absences', '#absences'), ('/api/student-support', 'Follow-up', '#follow-ups'), ('/api/mail', 'Mail', '#mail'),
         ('/api/exams', 'Exams', '#exams'), ('/api/gradebook', 'Gradebook', '#gradebook'), ('/api/calendar', 'Calendar', '#calendar'),
         ('/api/canvas', 'Canvas', '#canvas'), ('/api/grading', 'Grading', '#grading'),
         ('/api/student-notes', 'Student notes', ''), ('/api/iclicker', 'iClicker', ''), ('/api/question-bank', 'Question bank', ''),
         ('/api/accommodations', 'Accommodations', '#accommodations'))
PRIVATE = 'Saved in Faculty only. The student is not told.'
BOOKING_CARD = 'Saved in Faculty. The booking card above is all this step sends the student.'
# ONE ROUND: Waiting on student is proposed beside the reply and approved once it is sent (Approve all sends mail first).
WAITING = ['It can be approved once the reply is sent. Approve all sends the reply first.']
LONG = 2000  # the owners' longest text field (absence details, decision notes); anything longer reads in the exact request
# CANVAS PUSH CARDS: the Canvas plan's objects and fields (canvas/content.py `plan`) in the words of the Canvas page.
CANVAS_KINDS = {'assignment': 'assignment', 'page': 'page', 'module': 'module', 'item': 'module item', 'group': 'Assignment Group'}
CANVAS_FIELDS = {'due_at': 'due date', 'unlock_at': 'available-from date', 'lock_at': 'available-until date', 'name': 'name',
                 'title': 'title', 'description': 'description', 'body': 'text', 'points': 'points', 'grading_type': 'grading type',
                 'submission_types': 'submission types', 'group': 'Assignment Group', 'published': 'status',
                 'omit_from_final_grade': 'final-grade setting', 'front_page': 'front page', 'require_sequential_progress': 'sequential progress'}
CANVAS_TIMES = ('due_at', 'unlock_at', 'lock_at')
CANVAS_SHOWN = 12  # changes listed on a card; the rest are counted and read in the page's preview


def page(path):
    """(page name, address) of the Faculty page that owns a route, or ('Faculty', '')."""
    for prefix, name, href in PAGES:
        if path == prefix or path.startswith(prefix + '/'):
            return name, href
    return 'Faculty', ''


def day(value):
    try:
        return dates_text(value, value) if value else ''
    except ValueError:
        return str(value)


def clock(value):
    """'14:00' → '2:00 PM' (campus wall clock, never converted; scheduling.clock.display_clock)."""
    return display_clock(value)


def when(body):
    start = clock(body['time']) if body.get('time') else ''
    span = f'{start}–{clock(body["end_time"])}' if start and body.get('end_time') else start
    return ', '.join(part for part in (day(body.get('date')), span) if part)


def words(value):
    """A field's value as a person reads it: text as written, lists joined, nested objects as their visible fields."""
    if value is None or value == '':
        return ''
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, list):
        return ', '.join(filter(None, (words(each) for each in value)))
    if isinstance(value, dict):
        return '; '.join(f'{label(key)}: {words(each)}' for key, each in value.items() if key not in INTERNAL and not key.endswith('_id') and words(each))
    text = str(value).strip()
    return text if len(text) <= LONG else text[:LONG - 1].rstrip() + '…'


def label(key):
    return str(key).replace('_', ' ').strip().capitalize()


def canvas_time(value, clock_too=True):
    """A Canvas time (an instant, ISO) on the campus clock: "Wed, Oct 14, 11:55 PM"; "No date" when cleared."""
    if not value:
        return 'No date'
    try:
        at = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone(CAMPUS_ZONE)
    except ValueError:
        return str(value)
    return f"{day_label(at.date())}, {display_clock(at.strftime('%H:%M'))}" if clock_too else day_label(at.date())


def sections_text(numbers):
    """[507] → "section 507"; [507, 514] → "sections 507 and 514"."""
    numbers = [str(n) for n in numbers]
    if len(numbers) < 2:
        return f"section {''.join(numbers)}"
    return f"sections {', '.join(numbers[:-1])} and {numbers[-1]}"


def points_text(value):
    """Points as the gradebook writes them: "10", "9.5" (never "10.0000")."""
    try:
        return format(Decimal(str(value)).normalize(), 'f')
    except (InvalidOperation, ValueError):
        return str(value)


def grade(status, points, out_of=None):
    """A gradebook cell in words: "10 / 10", "Excused", "Missing", or "(empty)" for a cleared cell."""
    if status == 'scored':
        return f'{points_text(points)} / {points_text(out_of)}' if out_of not in (None, '') else points_text(points)
    return {'excused': 'Excused', 'missing': 'Missing'}.get(status, '(empty)')


def change(changes, name, value, before=None):
    """One line of the card's What changes. `before` (a revision's current value) is shown only when it differs."""
    value = words(value)
    entry = {'label': name, 'value': value or '(empty)'}
    if before is not None:
        before = words(before)
        if before == value:
            return
        entry['before'] = before or '(empty)'
    elif not value:
        return
    changes.append(entry)


def decision_choices(new, status):
    """DECISION ON THE CARD (ASSISTANT): the decisions an absence card's Decision dropdown offers, in the Absences page's order, as
    [{value, label}]. A new absence is never recorded as withdrawn (Decline leaves it unrecorded), unless the card says so."""
    return [{'value': key, 'label': label} for key, label in STATUSES.items() if key != 'withdrawn' or not new or status == 'withdrawn']


class Describer:
    """Reads what a run's waiting cards name (one catalog connection, the caller's) and says what each would do."""

    def __init__(self, db, transport, run, student, people, booking=None):
        self.db, self.transport, self.run, self.people, self.booking = db, transport, run, people, booking
        self.student = (student or {}).get('name') or 'this student'
        # A COORDINATOR'S FOLLOW-UP: the run's own conversation is with the course coordinator, and its cards say so.
        self.coordinator = isinstance(run['enrollment_id'], str) and run['enrollment_id'].startswith('coordinator:')
        self._names = None
        self.preview = None  # the card's latest preview (`describe`), for a step whose request alone does not say what it changes

    def name(self, enrollment):
        """A student's roster name; the follow-up's own student is "this student"."""
        if enrollment == self.run['enrollment_id']:
            return f'{self.student} (the coordinator)' if self.coordinator else f'{self.student} (this student)'
        if self._names is None:
            self._names = self.people() if self.people else {}
        return (self._names.get(enrollment) or {}).get('name') or 'another student'

    def section(self, section_id):
        row = self.db.execute('SELECT number FROM course_sections WHERE id=?', (section_id,)).fetchone() if section_id else None
        return f'Section {row[0]}' if row else ''

    def describe(self, action):
        """The card's `effect`, or None when its request no longer reads (a route since removed, a malformed body): the page then
        shows the card as the Assistant wrote it, and Approve still rechecks everything."""
        body = action.get('body') if isinstance(action.get('body'), dict) else {}
        preview = action.get('preview') if isinstance(action.get('preview'), dict) else {}
        self.preview = preview.get('data') if isinstance(preview.get('data'), dict) and not preview.get('error') else None
        try:
            endpoint, args = self.transport.match(action['method'], action['path'].split('?')[0])
            handler = getattr(self, '_' + str(endpoint).replace('.', '_'), None)
            name, href = page(action['path'])
            effect = {'where': name, 'href': href, 'changes': [], 'notes': []}
            effect.update(handler(body, args or {}) if handler else self._other(action, body, args or {}))
            if self.coordinator and str(endpoint).startswith('student_support.') and effect.get('own'):
                effect = self._with_coordinator(effect)
        except (ValueError, LookupError, TypeError, AttributeError, sqlite3.Error):
            return None
        return effect

    @staticmethod
    def _with_coordinator(effect):
        """A COORDINATOR'S FOLLOW-UP: its own tracking steps name the coordinator where a student's name the student ("Mark this
        follow-up waiting on the coordinator", "The coordinator is not told")."""
        def said(text):
            return (text.replace('Waiting on student', 'Waiting on coordinator').replace('the student', 'the coordinator')
                    .replace('The student', 'The coordinator')) if isinstance(text, str) else text
        return {**effect, **{key: said(effect[key]) for key in ('title', 'audience') if key in effect},
                'notes': [said(note) for note in effect.get('notes', [])],
                'changes': [{**entry, **{key: said(entry[key]) for key in ('value', 'before') if key in entry}} for entry in effect.get('changes', [])]}

    def done(self, action):
        """DONE FOR YOU: one line for a record the Assistant made by itself, in the past tense ({title, detail, href})."""
        effect = self.describe(action) or {}
        title = effect.get('title') or action.get('summary') or 'Recorded a change'
        past = {'Record an absence': 'Recorded an absence', 'Excuse an absence': 'Recorded an absence',
                'Schedule office hours': 'Put office hours on your calendar'}
        plan = title
        verbs = (('Attach ', 'Attached '), ('Mark ', 'Marked '), ('Snooze ', 'Snoozed '), ('Track ', 'Tracked '), ('Reopen ', 'Reopened '),
                 ('Hold ', 'Held '), ('Release ', 'Released '), ('Make ', 'Made '))
        title = past.get(title) or next((after + title[len(before):] for before, after in verbs if title.startswith(before)), title)
        detail = ' · '.join(c['value'] for c in effect.get('changes', [])
                            if c.get('label') not in ('Details', 'Private details') and len(c['value']) <= 60)
        return {'title': title, 'plan': plan, 'detail': detail[:240], 'href': effect.get('href', '')}

    # Who it is for: the follow-up's own student, or someone named (WIDE SCOPE cards say so). ------------------------------
    def _for(self, enrollment):
        own = enrollment == self.run['enrollment_id']
        return {'own': own, 'who': self.name(enrollment)} if enrollment else {'own': None, 'who': ''}

    # Mail ---------------------------------------------------------------------------------------------------------------------
    def _gmail_mail_send(self, body, args):
        reply = bool(body.get('reply_thread_id'))
        return {'title': 'Reply by email' if reply else 'Send an email', 'button': 'Send', 'own': True, 'sends': True,
                'audience': 'Goes only to the recipients above, and only when you press Send.'}

    # Absences -----------------------------------------------------------------------------------------------------------------
    def _absence_lines(self, facts, current=None, new=True):
        changes, current = [], current or {}
        first, last = facts.get('first_date', current.get('first_date')), facts.get('last_date', current.get('last_date'))
        try:
            dates = dates_text(first, last or first) if first else ''
        except ValueError:
            dates = f'{first} – {last}'
        change(changes, 'Dates', dates, dates_text(current['first_date'], current['last_date']) if current else None)
        reason = facts.get('reason', current.get('reason'))
        change(changes, 'Reason', REASONS.get(reason, reason), REASONS.get(current['reason']) if current else None)
        status = facts.get('status', current.get('status', 'pending'))
        change(changes, 'Decision', STATUSES.get(status, status), STATUSES.get(current['status']) if current else None)
        if status in STATUSES and (new or 'status' in facts):
            # DECISION ON THE CARD (requirement, 2026-10-12): the line names its choices (`choice`), so the page draws a dropdown. A
            # revision that names the decision the absence already has keeps its line, so the choice can be changed again.
            line = next((c for c in changes if c['label'] == 'Decision'), None)
            if line is None:
                line = {'label': 'Decision', 'value': STATUSES[status]}
                changes.append(line)
            line['choice'] = {'value': status, 'options': decision_choices(new, status)}
        change(changes, 'Details', facts.get('details', current.get('details', '')), current.get('details', '') if current else None)
        change(changes, 'Note on your decision', facts.get('decision_note', current.get('decision_note', '')),
               current.get('decision_note', '') if current else None)
        # RULE 7 NOTICE and DEATHS WAIT (ABSENCES): saved facts too, so a revision that sets only one of them never reads as
        # "Nothing visible changes" (requirement, 2026-10-06).
        notice = facts.get('notice_message_id', current.get('notice_message_id')) or None
        if notice or current:
            change(changes, 'Notice', self._notice(notice, first, last or first),
                   self._notice(current.get('notice_message_id'), current['first_date'], current['last_date']) if current else None)
        death = bool(facts.get('death', current.get('death')))
        if death or current:
            change(changes, 'A death', death, bool(current.get('death')) if current else None)
        return changes, status

    def _notice(self, message_id, first, last):
        """The student's message kept as their notice and when it came against Rule 7, in the Absences window's words."""
        if not message_id:
            return NOTICES['none']
        sent = self.db.execute('SELECT subject,sent_at FROM comm_revisions WHERE message_id=? ORDER BY revision DESC LIMIT 1',
                               (str(message_id),)).fetchone()
        if not sent:
            return 'A message not in their record'
        try:
            timing = notice_timing(first, last, sent['sent_at'])
            at = dt.datetime.fromisoformat(timing['at'].replace('Z', '+00:00')).astimezone(CAMPUS_ZONE)
            moment, label = f"{day_label(at.date())}, {display_clock(at.strftime('%H:%M'))}", timing['label']
        except (KeyError, TypeError, ValueError):
            moment, label = '', ''
        return ' · '.join(part for part in (sent['subject'] or 'Their message', moment, label) if part)

    def _iclicker_given(self, section_id, first, last):
        """ICLICKER AFTER CLASS: the iClicker sessions the section was given in these dates, as the after-class imports recorded
        them ("“Class 20” (Mon, Oct 5)"); [] when none is known yet."""
        try:
            from gradebook.service import GradebookService
            term = self.db.execute('SELECT term_id FROM course_sections WHERE id=?', (section_id,)).fetchone() if section_id and first else None
            if not term:
                return []
            course = GradebookService._iclicker_courses(self.db, term[0]).get(section_id)
            setup = self.db.execute('SELECT setup_json FROM gradebook_setups WHERE term_id=? ORDER BY revision DESC LIMIT 1', (term[0],)).fetchone()
            titles = {item['id']: item['title'] for item in json.loads(setup[0]).get('items', [])} if setup else {}
            return [f"“{titles.get(s['item_id'], 'an iClicker session')}” ({day_label(dt.date.fromisoformat(s['date']))})"
                    for s in GradebookService._iclicker_sessions(self.db, term[0])
                    if course and s['course_id'] == course and s['item_id'] and first <= s['date'] <= (last or first)]
        except (sqlite3.Error, ValueError, TypeError):  # a catalog without the gradebook's tables knows of no session
            return []

    def _absence_notes(self, status, was=None, section_id=None, first=None, last=None):
        if status == 'excused' and was != 'excused':
            given = self._iclicker_given(section_id, first, last)
            if given:
                more = f' and {len(given) - 4} more' if len(given) > 4 else ''
                return [f"Excusing it gives full credit for {', '.join(given[:4])}{more} (iClicker) and lists work given then as a makeup owed."]
            return ['Excusing it gives full credit for any iClicker session in these dates (each is imported after its class) and lists '
                    'work given then as a makeup owed.']
        if status == 'pending':
            return ['It waits on the Absences page for your decision. Nothing is excused and no grade changes.']
        if was == 'excused' and status != 'excused':
            return ['iClicker credit given for this excused absence is taken back.']
        return ['No grade changes.']

    def _booking_card(self, changes, status, was, section_id, enrollment_id, first, last, absence_id=None):
        """ABSENCES "MAKEUP BOOKING" › ON THE FOLLOW-UP (requirement, 2026-11-03): excusing this absence emails the student a booking
        card by itself, so the card's face says so and its audience does not say the student is not told. The audience to use."""
        if self.booking is None or status != 'excused' or was == 'excused' or not (section_id and enrollment_id and first):
            return PRIVATE
        try:
            quizzes = self.booking.excuse_cards(self.db, section_id, enrollment_id, first, last or first, absence_id)
            sender = self.booking.gmail_address() if quizzes else ''
        except (sqlite3.Error, ValueError, TypeError, KeyError):  # a word on the card; Approve is unaffected
            return PRIVATE
        if not quizzes:
            return PRIVATE
        changes.append({'label': 'Then', 'show': True, 'value': f"Faculty emails them a booking card for {' and '.join(quizzes)} by itself, "
                        f"from your Gmail{f' ({sender})' if sender else ''}, to pick a makeup time."})
        return BOOKING_CARD

    def _attachment_name(self, message_id, index):
        """The file name of an attachment kept in the communications record, or ''."""
        sent = self.db.execute('SELECT attachments_json FROM comm_revisions WHERE message_id=? ORDER BY revision DESC LIMIT 1',
                               (str(message_id or ''),)).fetchone()
        files = json.loads(sent['attachments_json'] or '[]') if sent else []
        return str(files[index].get('name') or '') if type(index) is int and 0 <= index < len(files) and isinstance(files[index], dict) else ''

    def _absences_create(self, body, args):
        changes, status = self._absence_lines(body)
        section = self.section(body.get('section_id'))
        if section:
            changes.insert(0, {'label': 'Section', 'value': section})
        documents = [d for d in body.get('documents') or [] if isinstance(d, dict)]
        notes = self._absence_notes(status, None, body.get('section_id'), body.get('first_date'), body.get('last_date'))
        if documents:
            # WITH DOCUMENTS (ABSENCES): the student's own files are kept with the absence by the same approval.
            names = [self._attachment_name(d.get('message_id'), d.get('index')) or 'an attachment' for d in documents]
            changes.append({'label': 'Documentation' if len(names) == 1 else f'Documentation ({len(names)} files)', 'value': ', '.join(names)})
            notes = ['Keeps the files the student sent with the absence as its documentation.', *notes]
        audience = self._booking_card(changes, status, None, body.get('section_id'), body.get('enrollment_id'), body.get('first_date'), body.get('last_date'))
        return {**self._for(body.get('enrollment_id')), 'title': 'Excuse an absence' if status == 'excused' else 'Record an absence',
                'button': 'Record absence', 'changes': changes, 'notes': notes, 'audience': audience}

    def _absences_revise(self, body, args):
        row = self.db.execute('''SELECT a.enrollment_id,r.* FROM absences a JOIN absence_revisions r ON r.absence_id=a.id
            WHERE a.id=? ORDER BY r.revision DESC LIMIT 1''', (args.get('absence_id'),)).fetchone()
        current = dict(row) if row else {}
        changes, status = self._absence_lines(body, current or None, new=False)
        was = current.get('status')
        title = ('Excuse an absence' if status == 'excused' and was != 'excused' else 'Mark an absence not excused' if status == 'not_excused' and was != 'not_excused'
                 else 'Withdraw an absence' if status == 'withdrawn' and was != 'withdrawn'
                 else 'Record their notice for an absence' if [c['label'] for c in changes] == ['Notice'] and body.get('notice_message_id')
                 else 'Change an absence record')
        section = self.db.execute('SELECT section_id FROM absences WHERE id=?', (args.get('absence_id'),)).fetchone() if current else None
        notes = self._absence_notes(status, was, section[0] if section else None, body.get('first_date', current.get('first_date')),
                                    body.get('last_date', current.get('last_date'))) if changes else ['Nothing visible changes.']
        if any(c['label'] == 'Notice' for c in changes):  # RULE 7 NOTICE: timing informs the decision and changes nothing by itself
            notes = ['Absences shows when they told you, against Student Rule 7. That decides nothing by itself.', *notes]
        audience = self._booking_card(changes, status, was, section[0] if section else None, current.get('enrollment_id'), body.get('first_date', current.get('first_date')),
                                      body.get('last_date', current.get('last_date')), args.get('absence_id'))
        return {**self._for(current.get('enrollment_id')), 'title': title, 'button': 'Save absence', 'changes': changes,
                'notes': notes, 'audience': audience}

    def _absences_attach_message(self, body, args):
        row = self.db.execute('''SELECT a.enrollment_id,r.first_date,r.last_date,r.status FROM absences a JOIN absence_revisions r ON r.absence_id=a.id
            WHERE a.id=? ORDER BY r.revision DESC LIMIT 1''', (args.get('absence_id'),)).fetchone()
        name = self._attachment_name(body.get('message_id'), body.get('index'))
        changes = [{'label': 'File', 'value': name or 'an attachment of the student’s message'}]
        if row:
            changes.append({'label': 'Absence', 'value': f"{dates_text(row['first_date'], row['last_date'])} · {STATUSES.get(row['status'], row['status'])}"})
        return {**self._for(row['enrollment_id'] if row else None), 'title': f'Attach {name or "a file"} to the absence record', 'button': 'Attach file',
                'changes': changes, 'audience': PRIVATE,
                'notes': ['Adds the file the student sent as documentation on the Absences page. It decides nothing by itself.']}

    def _absences_makeup_hold(self, body, args):
        """MAKEUP HOLDS: an absence's quiz makeups held (no booking card, no offered time) until the instructor releases them."""
        row = self.db.execute('''SELECT a.enrollment_id,r.first_date,r.last_date,r.status FROM absences a JOIN absence_revisions r ON r.absence_id=a.id
            WHERE a.id=? ORDER BY r.revision DESC LIMIT 1''', (args.get('absence_id'),)).fetchone()
        changes = []
        if row:
            changes.append({'label': 'Absence', 'value': f"{dates_text(row['first_date'], row['last_date'])} · {STATUSES.get(row['status'], row['status'])}"})
        if body.get('held') is not True:
            return {**self._for(row['enrollment_id'] if row else None), 'title': 'Release the makeup hold', 'button': 'Release', 'where': 'Makeups', 'href': '#absences?view=makeups',
                    'changes': changes, 'audience': PRIVATE,
                    'notes': ['Its quiz makeups book as usual again: with the term’s booking cards on, the card goes out shortly.']}
        if body.get('reason'):
            changes.append({'label': 'Waiting on', 'value': str(body['reason'])})
        if body.get('check_back'):
            try:
                changes.append({'label': 'Check back', 'value': day_label(dt.date.fromisoformat(body['check_back']))})
            except (TypeError, ValueError):
                changes.append({'label': 'Check back', 'value': str(body['check_back'])})
        return {**self._for(row['enrollment_id'] if row else None), 'title': 'Hold makeup booking for the absence', 'button': 'Hold', 'where': 'Makeups', 'href': '#absences?view=makeups',
                'changes': changes, 'audience': PRIVATE,
                'notes': ['No booking card goes out for its quiz makeups and no makeup time is offered until you release it on the '
                          'Makeups view. An unanswered card is withdrawn. Nothing is sent.']}

    # Follow-ups and office hours ---------------------------------------------------------------------------------------------
    @staticmethod
    def _support_lines(body, current=None):
        """What a follow-up or appointment write sets; with `current` (a revision), only what differs from it."""
        changes = []
        old = (lambda key: current.get(key, '')) if current is not None else (lambda key: None)
        if 'state' in body:
            change(changes, 'Status', STATES.get(body['state'], body['state']), STATES.get(old('state'), old('state')) if current is not None else None)
        for key, name in (('title', 'Purpose'), ('next', 'Next step'), ('location', 'Location'), ('details', 'Private details')):
            if key in body:
                change(changes, name, body[key], old(key))
        if 'commitments' in body:
            def tasks(rows):
                return '\n'.join(f"{c.get('title', '')} — {c.get('state', 'open')}; waits on {c.get('waiting_on', 'you')}"
                                 + (f"; check back {c['check_back']}" if c.get('check_back') else '')
                                 + ('; reply prerequisite' if c.get('blocks_reply') else '')
                                 for c in rows or [] if isinstance(c, dict))
            change(changes, 'Separate tasks', tasks(body['commitments']), tasks((current or {}).get('commitments')) if current is not None else None)
            if changes and changes[-1]['label'] == 'Separate tasks':
                changes[-1]['show'] = True
        if any(key in body for key in ('date', 'time', 'end_time')):
            merged = {key: body.get(key, (current or {}).get(key, '')) for key in ('date', 'time', 'end_time')}
            change(changes, 'When' if merged.get('time') else 'Follow-up date', when(merged),
                   when({key: (current or {}).get(key, '') for key in ('date', 'time', 'end_time')}) if current is not None else None)
        return changes

    def _student_support_create(self, body, args):
        effect = self._for(body.get('enrollment_id'))
        if body.get('kind') == 'appointment':
            return {**effect, 'title': 'Schedule office hours', 'button': 'Schedule', 'where': 'Office hours', 'href': '#office-hours',
                    'changes': self._support_lines(body),
                    'notes': ['Adds “Office hours” with the student’s name and its purpose at this time to your calendar.',
                              'The student is not told: say the time in your reply.'], 'audience': PRIVATE}
        this = effect['own'] and body.get('source_key', '') == self.run['source_key'] and self.run['source_key']
        title = ({'resolved': 'Mark this conversation resolved', 'waiting': 'Mark this conversation waiting on the student',
                  'todo': 'Mark this conversation To do',
                  'snoozed': 'Snooze this conversation until ' + (day(body.get('date')) or 'later')}.get(body.get('state'), 'Track this conversation')
                 if this else 'Add a follow-up')
        return {**effect, 'title': title, 'button': 'Save follow-up',
                'changes': self._support_lines(body), 'notes': WAITING if this and body.get('state') == 'waiting' else [], 'audience': PRIVATE}

    def _student_support_suggestion_redo(self, body, args):
        """REDO FROM CHAT: the chat's request to prepare this follow-up's suggestions again, shown when it waits for approval."""
        changes = []
        change(changes, 'Your changes', body.get('text') or 'None: prepare them again as they are now (Retry)')
        return {**self._for(body.get('enrollment_id')), 'title': 'Prepare the suggestions again', 'button': 'Prepare again',
                'where': 'Follow-up', 'href': '#follow-ups', 'changes': changes,
                'notes': ['Replaces the steps not yet approved here; completed steps stay.', 'Nothing is sent: every new step waits for you.'],
                'audience': PRIVATE}

    def _student_support_revise(self, body, args):
        # Against the revision the request was based on, so a change that already ran (Done for you) still reads as that change.
        base = body.get('base_revision') if isinstance(body.get('base_revision'), int) else None
        query = '''SELECT i.enrollment_id,i.kind,i.source_key,r.content_json FROM student_support_items i JOIN student_support_revisions r
            ON r.item_id=i.id WHERE i.id=? AND (? IS NULL OR r.revision=?) ORDER BY r.revision DESC LIMIT 1'''
        row = self.db.execute(query, (args.get('item_id'), base, base)).fetchone() or (
            self.db.execute(query, (args.get('item_id'), None, None)).fetchone() if base is not None else None)
        current = json.loads(row['content_json']) if row else {}
        effect = self._for(row['enrollment_id'] if row else None)
        changes = self._support_lines(body, current)
        if row and row['kind'] == 'appointment':
            moved = any(entry['label'] == 'When' for entry in changes)
            return {**effect, 'title': 'Change an office-hours appointment', 'button': 'Save appointment', 'where': 'Office hours',
                    'href': '#office-hours', 'changes': changes, 'audience': PRIVATE,
                    'notes': ['Moves its “Office hours” event on your calendar. The student is not told.'] if moved else []}
        # This conversation's own follow-up, also when it was first tracked after the preparation began (its item_id was empty then).
        this = (args.get('item_id') == self.run['item_id'] and self.run['item_id']) or (
            row is not None and self.run['source_key'] and row['source_key'] == self.run['source_key'])
        state = body.get('state') if body.get('state') != current.get('state') else None
        title = {'resolved': 'Mark {} resolved', 'waiting': 'Mark {} waiting on the student', 'needs_reply': 'Reopen {}', 'todo': 'Mark {} To do',
                 'snoozed': 'Snooze {} until ' + (day(body.get('date')) or 'later')}.get(state, 'Update {}')
        title = title.format('this follow-up' if this else 'another follow-up')
        return {**effect, 'title': title, 'button': 'Save follow-up', 'changes': changes, 'audience': PRIVATE,
                'notes': (WAITING if this and state == 'waiting' else []) if changes else ['Nothing visible changes.']}

    # Notes, exams, regrades ----------------------------------------------------------------------------------------------------
    def _student_notes_create(self, body, args):
        changes = []
        change(changes, 'Date', day(body.get('date')))
        change(changes, 'Note', body.get('text'))
        return {**self._for(body.get('enrollment_id')), 'title': 'Add a private note', 'button': 'Save note', 'changes': changes,
                'where': 'Student notes', 'href': '', 'audience': 'Private to you. The student never sees it.'}

    def _dr_quiz_takers_save(self, body, args):
        on = body.get('on') is True  # ACCOMMODATIONS "DR QUIZ TAKERS": an Approve card
        return {**self._for(args['enrollment_id']), 'title': 'Mark as a DR quiz taker' if on else 'No longer a DR quiz taker', 'button': 'Save', 'where':
                'Accommodations', 'href': '#accommodations', 'changes': [{'label': 'DR quiz taker', 'value': 'Yes' if on else 'No'}], 'audience': PRIVATE}

    def _absences_department_makeup(self, body, args):
        """ABSENCES "DEPARTMENT MAKEUP EXAMS" (requirement, 2026-10-22: a card they approve): the department's makeup exam request
        as the form would receive it, from the card's latest preview. The session and each exam rule are dropdowns (`choice` with
        its `field`): a change is saved on the card as the instructor's own (EDIT IN PLACE), and nothing is sent until Approve."""
        shown = self.preview or {}
        enrollment = shown.get('enrollment_id') or (self.db.execute('SELECT enrollment_id FROM absences WHERE id=?',
                                                                    (args.get('absence_id'),)).fetchone() or [None])[0]
        changes, notes = [], []
        choices = shown.get('choices') if isinstance(shown.get('choices'), dict) else {}
        answers = shown.get('answers') if isinstance(shown.get('answers'), dict) else {}
        for line in shown.get('shown') or []:
            if not isinstance(line, dict):
                continue
            entry = {'label': str(line.get('label') or ''), 'value': str(line.get('value') or '')}
            key = line.get('key')
            options = [o for o in choices.get(key) or [] if isinstance(o, str)]
            if key in ('session', 'scantron', 'documentation', 'calculator', 'reset', 'time') and options:
                # The time's dropdown is the exam's own time; the student's extra time is applied to it (the line says so).
                value = shown.get('base_time') if key == 'time' else answers.get(key)
                value = str(value or '')
                listed = ([] if value in options or not value else [value]) + options
                entry['choice'] = {'field': key, 'value': value,
                                   'options': ([{'value': '', 'label': 'Choose…'}] if not value else []) + [{'value': o, 'label': o} for o in listed]}
                if key == 'time' and shown.get('extra_time') and value:
                    # The dropdown is the exam's own time; the form gets it with their extra time, its own line.
                    changes.append({**entry, 'label': "Exam's time"})
                    entry = {'label': f"Their time ({shown['extra_time']})", 'value': str(answers.get('time') or '')}
            changes.append(entry)
        if not changes:
            changes.append({'label': 'Exam', 'value': str(body.get('assignment_id') or '')})
            notes.append('The form could not be read just now; Approve reads it again before anything is sent.')
        notes += [str(w) for w in shown.get('warnings') or []]
        if shown.get('missing'):
            notes.append('Choose ' + ', '.join(str(m) for m in shown['missing']) + ' before sending.')
        return {**self._for(enrollment), 'title': f"Request a department makeup of {shown.get('title') or 'the exam'}",
                'button': 'Send request', 'where': 'Absences', 'href': '#absences', 'changes': changes, 'notes': notes, 'sends': True,
                'audience': (f"Fills out the Math department's makeup exam request form (Google Forms), recording "
                             f"{shown.get('email') or 'your TAMU email'}, only when you press Send request. Once Google accepts it, the "
                             "student is emailed from your Gmail with the makeup's day, time and room, and that missing it means a 0.")}

    def _scheduling_aim_upload(self, body, args):
        """SCHEDULING "DR REQUEST EMAILS" (requirement, 2026-11-01): one DR quiz's accessible copy uploaded to the students'
        requests in AIM, after the saved school sign-in and the instructor's Duo in the window Approve opens."""
        item, _, section = str(body.get('key') or '').partition(':')
        found = self.db.execute('SELECT items_json FROM scheduling_plan_revisions WHERE term_id=? ORDER BY revision DESC LIMIT 1',
                                (args.get('term_id'),)).fetchone()
        rows = json.loads(found[0]) if found else []
        title = next((str(row.get('title') or '') for row in rows if isinstance(row, dict) and row.get('item') == item), '') or 'the quiz'
        number = self.db.execute('SELECT number FROM course_sections WHERE id=?', (section,)).fetchone()
        changes = [{'label': 'Quiz', 'value': title}] + ([{'label': 'Section', 'value': str(number[0])}] if number else [])
        return {**self._for(body.get('enrollment_id')), 'title': f"Upload {title}'s accessible copy to AIM", 'button': 'Approve upload',
                'where': 'Accommodations', 'href': '#accommodations?tab=quizzes', 'changes': changes, 'sends': True,
                'notes': ['Opens AIM’s sign-in here: approve the Duo push, then Faculty uploads the copy.'],
                'audience': ("Signs in to AIM with your saved NetID sign-in and uploads the quiz's accessible copy to every request of "
                             "this section for that day that does not have it, then imports the DR bookings. Nothing is sent to the "
                             "student.")}

    def _exam_title(self, event_id):
        row = self.db.execute('SELECT title FROM exam_events WHERE id=?', (event_id,)).fetchone()
        return row[0] if row and row[0] else 'the exam'

    def _exams_sitting(self, body, args, verb='Add'):
        changes = []
        for key, name in (('label', 'Name'), ('location', 'Location')):
            change(changes, name, body.get(key))
        change(changes, 'When', when(body))
        makeup = 'makeup ' if body.get('kind') == 'makeup' else ''
        people = [p for p in body.get('participants') or [] if isinstance(p, dict)]
        if not people:
            return {'own': None, 'who': '', 'title': f'{verb} a {makeup}sitting of {self._exam_title(args.get("event_id"))}',
                    'button': f'{verb} sitting', 'changes': changes, 'audience': PRIVATE,
                    'notes': ['Changes the exam’s plan only. No student is moved into the sitting by this step.']}
        # EXAMS WITH PARTICIPANTS: the sitting and the students it seats are one approval.
        names = [self.name(p.get('enrollment_id')) for p in people]
        changes.append({'label': 'Seats', 'value': ', '.join(names)})
        return {'own': all(p.get('enrollment_id') == self.run['enrollment_id'] for p in people),
                'who': names[0] if len(names) == 1 else f'{len(names)} students',
                'title': f'{verb} a {makeup}sitting of {self._exam_title(args.get("event_id"))} and seat {"the student" if len(names) == 1 else "the students"}',
                'button': f'{verb} sitting', 'changes': changes, 'audience': PRIVATE,
                'notes': ['Adds the sitting to the exam’s plan and seats the named students in it. Their plan stays as it was otherwise.']}

    def _exams_change_sitting(self, body, args):
        return self._exams_sitting(body, args, 'Change')

    def _exams_participant(self, body, args):
        changes = []
        if 'expectation' in body:
            change(changes, 'Plan', EXPECTATIONS.get(body['expectation'], body['expectation']))
        change(changes, 'Reason', body.get('reason'))
        if body.get('sitting_id'):
            row = self.db.execute('SELECT label,date,time FROM exam_sittings WHERE id=?', (body['sitting_id'],)).fetchone()
            change(changes, 'Sitting', ' · '.join(filter(None, (row['label'], when(dict(row))))) if row else 'a sitting')
        return {**self._for(args.get('enrollment_id')), 'title': f'Change the plan for {self._exam_title(args.get("event_id"))}',
                'button': 'Save exam plan', 'changes': changes, 'audience': PRIVATE}

    def _frq_regrades_reply(self, body, args):
        return {'own': True, 'who': f'{self.student} (this student)', 'title': 'Answer the regrade request on Gradescope',
                'button': 'Post answer', 'sends': True, 'changes': self._fields(body),
                'audience': 'Posted on Gradescope when you approve; the student sees it there.'}

    def _frq_regrade_panel_decide(self, body, args):
        """REGRADES "FOLLOW-UP": keep or change the grade and answer the regrade request, carried everywhere by Apply. The preview
        (`decide_preview`) says what each step changes; without one, the request alone."""
        shown = self.preview or {}
        of = shown.get('of')
        grade = lambda value: f'{value} / {of}' if value is not None and of is not None else value  # noqa: E731
        changes = []
        if body.get('decision') == 'change' and 'answer' in body:
            # REGRADES "BUBBLE SHEETS": the reading of one bubble-sheet question, which the key then scores.
            change(changes, 'Grade', grade(shown.get('to')) or 'changed (see the preview)', grade(shown.get('from')))
            letters = lambda value: ' and '.join(value) if value else 'a blank'  # noqa: E731
            number = f"Question {shown['number']}" if shown.get('number') else 'The question'
            read = f" (Faculty read {letters(shown['read'])})" if isinstance(shown.get('read'), str) else ''
            key = f"; your key: {shown['key']}" if shown.get('key') else ''
            change(changes, 'Reading', f"{number}: {letters(str(body.get('answer') or ''))}{read}{key}")
            changes[-1]['show'] = True
        elif body.get('decision') == 'change':
            change(changes, 'Grade', grade(shown.get('to')) or 'changed (see the preview)', grade(shown.get('from')))
            # Each line as the rubric numbers and words it when the preview says (`line_words`), else its ID; one per row, on the
            # card's summary like the reply (what the grade change IS).
            words = shown.get('line_words') if isinstance(shown.get('line_words'), dict) else {}
            lines = [*(f'+ {words.get(line) or line}' for line in next((s.get('added') for s in shown.get('steps') or [] if s.get('step') == 'grade'), []) or []),
                     *(f'− {words.get(line) or line}' for line in next((s.get('removed') for s in shown.get('steps') or [] if s.get('step') == 'grade'), []) or [])]
            if lines:
                change(changes, 'Rubric lines', '\n'.join(lines))
                changes[-1]['show'] = True
            if isinstance(body.get('adjustment'), dict):
                change(changes, 'Point adjustment', f"{body['adjustment'].get('points')} · {body['adjustment'].get('reason') or ''}")
        else:
            change(changes, 'Grade', f'Stays {grade(shown.get("from"))}' if shown.get('from') is not None else 'Stays as it is')
        change(changes, 'Reply', body.get('reply'))
        changes[-1]['show'] = True  # requirement, 2026-10-07: the reply is visible on the card's summary, never folded
        change(changes, 'Why', body.get('reason'))
        before = lambda step: step['from'] if step.get('from') is not None else '–'  # zero is a recorded grade
        words_ = {'gradescope': lambda s: f"Gradescope {before(s)} → {s.get('to')}" + (' (published: the student sees it at once)' if s.get('published') else ''),
                  'reply': lambda s: 'the reply, once Gradescope shows the grade (it closes the request)',
                  'gradebook': lambda s: s.get('problem') or f"the gradebook {before(s)} → {s['to'] if s.get('to') is not None else 'the certified value'} (certifying just this change)",
                  'canvas': lambda s: s.get('problem') or f"Canvas {before(s)} → {s.get('to')}"}
        steps = [words_[s['step']](s) for s in shown.get('steps') or [] if s.get('step') in words_]
        notes = ['Then, in order: ' + '; '.join(steps) + '.'] if steps else []
        notes += [n for n in shown.get('notes') or [] if isinstance(n, str)]
        title = (f'Change the grade to {grade(shown.get("to"))} and answer the regrade request' if body.get('decision') == 'change' and shown.get('to') is not None
                 else 'Change the grade and answer the regrade request' if body.get('decision') == 'change' else 'Keep the grade and answer the regrade request')
        return {'own': True, 'who': f'{self.student} (this student)', 'title': title, 'button': 'Apply', 'sends': True, 'changes': changes,
                'notes': notes, 'audience': 'When you approve: the grade on Gradescope, the reply there (the student sees both), the gradebook and Canvas.'}

    def _frq_grading_edit_rubric(self, body, args):
        """RUBRIC NOTE CARDS (requirement, 2026-10-21): which question, each line's note or wording as it is and as it would be, and
        how many automatic grades go back to review. Any change to a question's rubric or its notes ends the finalization its
        automatic grades were accepted under (GRADING), so they wait for review again; certified scores stay as recorded."""
        aid, qid = args['aid'], args['qid']
        row = self.db.execute('''SELECT e.title, q.exam_version, v.content_json, b.rules_json FROM frq_assignment_questions p
                JOIN frq_assignments f ON f.id=p.assignment_id JOIN exam_events e ON e.id=f.event_id JOIN frq_questions q ON q.id=p.question_id
                JOIN frq_question_revisions v ON v.id=p.question_revision_id JOIN frq_rubric_revisions b ON b.id=p.rubric_id
                WHERE p.assignment_id=? AND p.question_id=?''', (aid, qid)).fetchone()
        content, items = json.loads(row[2]), json.loads(row[3])['items']
        number = content.get('printed_number')
        question = ' '.join(filter(None, [row[0], f'Version {row[1]}' if row[1] else '', f'Question {number}' if number else content.get('title')]))
        lines = {item['id']: (n, item) for n, item in enumerate(items, 1)}
        plain = lambda text: ' '.join(str(text or '').replace('$$', '').replace('$', '').split())  # noqa: E731  no typeset math on a card
        # Short labels: the line's own words are a row of their own, so a long line never squeezes the text beside it.
        named = lambda iid: f'Line {lines[iid][0]}'  # noqa: E731
        signed = lambda value: points_text(value).replace('-', '−')  # noqa: E731
        shown = set()

        def line_row(iid):
            if iid not in shown:
                shown.add(iid)
                changes.append({'label': named(iid), 'value': f'{signed(lines[iid][1]["points"])} {plain(lines[iid][1]["description"])}', 'show': True})
        notes = {iid: note for iid, note in self.db.execute('''SELECT c.item_id,c.comment FROM frq_rubric_line_comments c
                WHERE c.assignment_id=? AND c.question_id=? AND c.rowid=(SELECT MAX(n.rowid) FROM frq_rubric_line_comments n
                WHERE n.assignment_id=c.assignment_id AND n.question_id=c.question_id AND n.item_id=c.item_id)''', (aid, qid))}
        changes = []
        for line in body.get('lines') or []:
            iid = line.get('item_id')
            if iid in lines:
                line_row(iid)
                change(changes, 'Wording', plain(line.get('description', lines[iid][1]['description'])), plain(lines[iid][1]['description']))
                change(changes, 'Points', signed(line.get('points', lines[iid][1]['points'])), signed(lines[iid][1]['points']))
        for iid, note in (body.get('comments') or {}).items():
            if iid not in lines:
                continue
            old, new = (notes.get(iid) or '').strip(), str(note or '').strip()
            if old == new:
                continue
            line_row(iid)
            if old and new.startswith(old):  # the common case: guidance added to the note, shown alone
                changes.append({'label': 'Note adds', 'value': new[len(old):].strip(), 'show': True})
            else:
                change(changes, 'Note', new or '(cleared)', old)
                changes[-1]['show'] = True
        reopened = self.db.execute('''SELECT COUNT(DISTINCT a.response_id) FROM frq_all_acceptances a JOIN frq_responses r ON r.id=a.response_id
                WHERE a.assignment_id=? AND a.question_id=? AND a.basis IN ('team_consensus','one_shot','tag_team') AND a.active=1
                AND r.current_result_id=a.result_id AND r.current_review_id IS NULL''', (aid, qid)).fetchone()[0]
        if changes:
            changes.append({'label': 'Grades', 'show': True, 'value': (
                f'{reopened} automatic grade{"s" if reopened != 1 else ""} on this question go{"es" if reopened == 1 else ""} back to review in Grading. '
                'Grades you reviewed stay.' if reopened else 'No automatic grade on this question goes back to review.')})
        noun = 'note' if not body.get('lines') else 'rubric'
        return {'own': None, 'who': 'every student who answered it', 'title': f'Change the {noun} on {question}', 'button': 'Save', 'changes': changes,
                'notes': ['Graders read the new guidance from now on, after the rubric is finalized again. Certified scores stay as recorded.']}

    def _assistant_code_task_start(self, body, args):
        return {'own': None, 'who': '', 'title': 'Ask a coding agent to change Faculty', 'button': 'Start the coding agent',
                'audience': 'Works in its own copy of Faculty. Nothing changes until you Apply its result in the Code tab.'}

    # Gradebook ------------------------------------------------------------------------------------------------------------------
    # GRADES ON THE CARD (requirement, 2026-10-09): each cell by its column (and student, when several),
    # the grade it replaces (the revision the request names, `base`; none is an empty cell) struck through beside the new one, on
    # the card's summary; the cell's note is a long field like any other.
    def _gradebook_write_scores(self, body, args):
        found = self.db.execute('SELECT setup_json FROM gradebook_setups WHERE term_id=? ORDER BY revision DESC LIMIT 1',
                                (args.get('term_id'),)).fetchone()
        items = {item.get('id'): item for item in json.loads(found[0]).get('items') or []} if found else {}
        cells = [cell for cell in body.get('changes') or [] if isinstance(cell, dict)]
        students = list(dict.fromkeys(str(cell.get('row')).rsplit(':', 1)[1] for cell in cells))
        changes, titles, grades = [], [], []
        for cell in cells:
            enrollment = str(cell['row']).rsplit(':', 1)[1]
            item = items.get(cell.get('item_id')) or {}
            based = self.db.execute('SELECT status,points FROM gradebook_scores WHERE id=? AND term_id=?',
                                    (cell['base'], args.get('term_id'))).fetchone() if cell.get('base') else None
            title = item.get('title') or 'a column'
            value, before = grade(cell.get('status'), cell.get('points'), item.get('points')), grade(*based, item.get('points')) if based else '(empty)'
            titles.append(title)
            grades.append((cell.get('status'), value, before))
            if len(changes) < CANVAS_SHOWN:
                entry = {'label': title if len(students) < 2 else f'{title} · {self.name(enrollment)}', 'value': value, 'show': True}
                changes.append(entry if before == value else {**entry, 'before': before})
        if len(cells) > CANVAS_SHOWN:
            changes.append({'label': 'And', 'value': f'{len(cells) - CANVAS_SHOWN} more (the exact request lists them)'})
        for note in dict.fromkeys(cell['note'].strip() for cell in cells if isinstance(cell.get('note'), str) and cell['note'].strip()):
            change(changes, 'Note', note)
        if len(cells) == 1:
            (status, value, before), column = grades[0], f'“{titles[0]}”'
            title = (f'Change the note of {column}' if value == before else f'Excuse {column}' if status == 'excused'
                     else f'Mark {column} missing' if status == 'missing' else f'Clear the grade of {column}' if status == 'cleared'
                     else f'Change the grade of {column} to {value}')
        else:
            title = f'Change {len(cells)} grades of “{titles[0]}”' if len(set(titles)) == 1 else f'Change {len(cells)} grades on Gradebook'
        named = self._for(students[0]) if len(students) == 1 else {'own': None, 'who': f'{len(students)} students'}
        return {**named, 'title': title, 'button': 'Save grade' if len(cells) == 1 else 'Save grades', 'changes': changes,
                'audience': 'Saved in the gradebook; the next Sync with Canvas sends it to Canvas.'}

    # ICLICKER CREDIT BEFORE THE IMPORT (iclicker.md; requirement, 2026-10-21): full credit for one class day's iClicker, now or
    # once that day is imported; the card says which, from the sessions recorded so far.
    def _gradebook_iclicker_credit(self, body, args):
        day = str(body.get('class_date') or '')
        when = day_label(dt.date.fromisoformat(day))
        given = self._iclicker_given(body.get('section_id'), day, day)
        changes = [{'label': 'Class day', 'value': when, 'show': True}]
        section = self.section(body.get('section_id'))
        if section:
            change(changes, 'Section', section)
        change(changes, 'Note', f"{' '.join(str(body.get('reason') or '').split())}: full credit.")
        audience = (f"Full credit for {', '.join(given)} in the gradebook now; the next Sync with Canvas sends it." if given else
                    "Waits for that day's iClicker: when it is imported after class, full credit is given by itself, then the next Sync "
                    "with Canvas sends it. Nothing is given if no iClicker was given that day.")
        return {**self._for(str(body.get('enrollment_id') or '')), 'title': f'Give full iClicker credit for {when}', 'button': 'Give credit',
                'changes': changes, 'audience': audience}

    def _gradebook_iclicker_credit_withdraw(self, body, args):
        found = self.db.execute('SELECT enrollment_id,class_date,reason FROM gradebook_iclicker_credits WHERE credit_id=? ORDER BY created_at DESC LIMIT 1',
                                (args.get('credit_id'),)).fetchone()
        when = day_label(dt.date.fromisoformat(found[1])) if found else 'a class day'
        return {**self._for(found[0] if found else ''), 'title': f'Take back the iClicker credit for {when}', 'button': 'Take back',
                'changes': [{'label': 'Credit', 'value': found[2] if found else 'an iClicker credit', 'show': True}],
                'audience': 'What the credit filled in gets back the grade it replaced; the next Sync with Canvas sends it.'}

    # A CANVAS QUIZ'S GRADE (canvas ONE CELL; requirement, 2026-10-13): the grade is set in Canvas, where the quiz is graded. The
    # card shows it like any grade change, beside what Canvas showed when the card was made (its preview).
    def _gradebook_canvas_quiz_grade(self, body, args):
        found = self.db.execute('SELECT setup_json FROM gradebook_setups WHERE term_id=? ORDER BY revision DESC LIMIT 1',
                                (args.get('term_id'),)).fetchone()
        item = next((i for i in (json.loads(found[0]).get('items') or [] if found else []) if i.get('id') == body.get('item_id')), {})
        title, shown = item.get('title') or 'a quiz', (self.preview or {}).get('canvas')
        value = grade('scored', body.get('points'), item.get('points'))
        before = grade(shown.get('status'), shown.get('points'), item.get('points')) if isinstance(shown, dict) else '(empty)'
        entry = {'label': title, 'value': value, 'show': True}
        return {**self._for(str(body['row']).rsplit(':', 1)[1]), 'where': 'Canvas', 'title': f'Change the grade of “{title}” to {value} in Canvas',
                'button': 'Set grade in Canvas', 'changes': [entry if before == value else {**entry, 'before': before}],
                'audience': 'Set in Canvas, where this quiz is graded. The gradebook shows it after the next Sync with Canvas.'}

    # Canvas ---------------------------------------------------------------------------------------------------------------------
    # CANVAS PUSH CARDS (requirement, 2026-10-06): a push names what it changes from its preview, the plan Approve runs (a changed plan is refused).
    @staticmethod
    def _canvas_step(op):
        """What one planned Canvas operation does, in a sentence, and the fields it sets [(name, value)]."""
        verb, _, kind = str(op.get('op') or '').partition('_')
        noun, name = CANVAS_KINDS.get(kind, 'object'), f"“{op.get('label') or 'untitled'}”"
        fields = op.get('fields') if isinstance(op.get('fields'), dict) else {}
        shown = [(CANVAS_FIELDS.get(key, label(key).lower()),
                  canvas_time(value) if key in CANVAS_TIMES else 'New text' if key in ('description', 'body')
                  else ('Published' if value else 'Unpublished') if key == 'published' else words(value) or '(empty)')
                 for key, value in fields.items()]
        if op.get('op') == 'create_group':
            return f"Add the {op.get('label') or 'Assignment Group'}", shown  # its label names it: Assignment Group “…”
        if op.get('op') == 'upload_file':  # canvas FILES: its label names it, File <folder>/<name>
            return f"Put the file “{str(op.get('label') or 'untitled').removeprefix('File ')}” in Canvas’s Files", shown
        if verb == 'create':
            return f'Add the {noun} {name}', shown
        if verb == 'delete':
            return f'Remove the {noun} {name}', shown
        if verb in ('move', 'replace'):
            return f'{verb.capitalize()} the module item {name}', shown
        if op.get('op') == 'order_modules':
            return 'Put the modules in order', shown
        if op.get('op') == 'order_items':
            return f'Put the items of {name} in order', shown
        if list(fields) == ['due_at'] and fields['due_at']:
            return f"Move the due date of {name} to {canvas_time(fields['due_at'], clock_too=False)}", shown
        names = [each for each, _ in shown]
        if names:
            joined = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
            return f'Change the {joined} of {name}', shown
        return f'Update the {noun} {name}', shown

    def _canvas_push(self, fallback, place_numbers=()):
        """A push card from its preview: a single change titled by what it does and where, several counted and listed.
        `fallback` titles the card while its preview does not read (the page's preview then says why)."""
        courses = [c for c in (self.preview or {}).get('courses') or [] if isinstance(c, dict)]
        steps, blocked = [], []  # [sentence, fields, section numbers], one per distinct change across the courses
        for course in courses:
            numbers = [s['number'] for s in course.get('sections') or [] if isinstance(s, dict) and s.get('number') is not None]
            for op in course.get('ops') or []:
                if isinstance(op, dict):
                    sentence, fields = self._canvas_step(op)
                    found = next((s for s in steps if s[0] == sentence and s[1] == fields), None)
                    if found:
                        found[2].extend(n for n in numbers if n not in found[2])
                    else:
                        steps.append([sentence, fields, list(numbers)])
            blocked += [b['label'] for b in course.get('blocked') or [] if isinstance(b, dict) and b.get('label')]
        reached = sorted({n for step in steps for n in step[2]}) or sorted(place_numbers)
        place = sections_text(reached) if reached else ''
        changes, notes = [], []
        if self.preview is None:
            title = fallback
        elif not steps:
            title = 'Nothing changes on Canvas'
        elif len(steps) == 1:
            title = f'{steps[0][0]} in {place}' if place else steps[0][0]
            changes = [{'label': each.capitalize(), 'value': value} for each, value in steps[0][1]]
        else:
            title = f'Make {len(steps)} changes in {place}' if place else f'Make {len(steps)} changes on Canvas'
            for sentence, fields, numbers in steps[:CANVAS_SHOWN]:
                due_only = sentence.startswith('Move the due date')
                value = f"{sentence} ({'; '.join(f'{each}: {v}' for each, v in fields)})" if fields and not due_only else sentence
                if sorted(numbers) != reached:  # a change only some of the sections get says which
                    value += f', {sections_text(sorted(numbers))} only'
                changes.append({'label': f'Change {len(changes) + 1}', 'value': value})
            if len(steps) > CANVAS_SHOWN:
                changes.append({'label': 'More', 'value': f'{len(steps) - CANVAS_SHOWN} more in the page’s preview'})
        if blocked:
            more = f' and {len(blocked) - 4} more' if len(blocked) > 4 else ''
            notes.append(f"Not pushed: {', '.join(blocked[:4])}{more}. The page’s preview says why.")
        return {'own': None, 'who': f'Everyone in {place}' if place else '', 'title': title, 'button': 'Push to Canvas',
                'changes': changes, 'notes': notes,
                'audience': f'Changes Canvas when you approve; students in {place} see it there.' if place
                else 'Changes Canvas when you approve; students see it there.'}

    def _canvas_section_content_push(self, body, args):
        row = self.db.execute('SELECT number FROM course_sections WHERE id=?', (args.get('section_id'),)).fetchone()
        numbers = [row[0]] if row else []
        return self._canvas_push(f'Change {sections_text(numbers)}’s Canvas course' if row else 'Change a section’s Canvas course', numbers)

    def _canvas_content_push(self, body, args):
        return self._canvas_push('Push the Canvas copy to its sections')

    # Anything else a WIDE SCOPE card may propose ---------------------------------------------------------------------------------
    def _fields(self, body):
        changes = []
        for key, value in body.items():
            if key not in INTERNAL and not key.endswith('_id'):
                change(changes, label(key), value)
        return changes

    def _documents_student_copy(self, body, args):
        """QUIZZES "A COPY BY EMAIL": one student's own stamped copy of an assignment, for a reply to attach."""
        row = self.db.execute('''SELECT a.title FROM assignment_versions v JOIN assignments a ON a.id=v.assignment_id WHERE v.id=?''',
                              (args.get('version_id'),)).fetchone()
        title = row[0] if row else 'an assignment'
        return {**self._for(body.get('enrollment_id')), 'title': f'Make their own copy of {title}', 'button': 'Make', 'where': 'Faculty',
                'href': '', 'changes': [{'label': 'Assignment', 'value': title}], 'audience': PRIVATE,
                'notes': ['A copy stamped for this student alone, for the reply to attach. It reaches them only with a reply you send, '
                          'and is never printed once emailed.']}

    def _other(self, action, body, args):
        name, _ = page(action['path'])
        verb = {'DELETE': 'Remove', 'PATCH': 'Change', 'PUT': 'Replace'}.get(action['method'], 'Save')
        enrollment = args.get('enrollment_id') or body.get('enrollment_id')
        named = self._for(enrollment) if enrollment else {
            'own': True if self.run['enrollment_id'] in json.dumps([args, body], default=str) else None, 'who': ''}
        if named['own'] and not named['who']:
            named['who'] = f'{self.student} (this student)'
        return {**named, 'title': f'{verb} a change on {name}' if name != 'Faculty' else f'{verb} a change in Faculty', 'button': 'Approve',
                'changes': self._fields(body), 'notes': ['Read the exact request below before approving.']}
