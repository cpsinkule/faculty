"""ASSISTANT: enrollment-owned context projection and bounded freshness receipts.

No copied correspondence is stored. Source revisions come from their owners;
the model cannot supply or authorize a freshness fingerprint.
"""
import json
from datetime import date, timedelta
from urllib.parse import urlencode

from werkzeug.exceptions import Conflict, NotFound
from communications.record import utc
from rosters import connect
from course_storage import timestamp
from scheduling.clock import campus_today
from student_support.service import conversations, conversation_of, joins
from .evidence import digest


LABELS = {'policies': 'term policies', 'notes': 'student notes', 'absences': 'absence records',
          'grades': 'grades or due dates', 'calendar': 'calendar availability', 'messages': 'other communications',
          'accommodations': 'accommodations or DR bookings', 'papers': 'received papers', 'schedule': 'term schedule',
          'class_schedule': 'class schedule', 'decisions': 'absence decisions'}
# WHAT CAN MAKE A STEP WRONG (requirement, 2026-10-08): approval is refused only for a calendar change at a time the draft books or names, a paper that arrived, or a
# changed absence decision. Every other change is a warning on the review, which Retry applies or the instructor approves past.
BLOCKING = ('calendar', 'papers', 'decisions')
VERSION = 2  # fingerprints kept with each event's time (v1, before 2026-10-08, kept a digest only: compared as warnings)
WHOLE_DAY = (0, 24 * 60)


class StudentContext:
    def __init__(self, owner):
        self.owner = owner

    def _read_coordinator(self, owner, term_id, source_key=''):
        """A COORDINATOR'S FOLLOW-UP: the same projection for a conversation with a term's coordinator, named by the term's owner
        key. It names the coordinator (`coordinator`), never a student: no notes, grades or roster address; the reply goes to the
        address the term's record holds, in the thread of the coordinator's latest email."""
        from course_coordinators import service as coordinators
        o = self.owner
        with connect(o.path) as db:
            term = db.execute('SELECT * FROM course_terms WHERE id=? AND deleted_at IS NULL', (term_id,)).fetchone()
            if not term:
                raise NotFound('That coordinator was not found.')
            who = coordinators.of_owner(db, owner)
            threads = conversations(db, owner)
            if source_key:
                if (owner, source_key) not in threads:
                    raise NotFound('That conversation is not with this coordinator.')
            elif threads:
                source_key = max(threads, key=lambda k: threads[k]['latest'])[1]
            item_key = 'conversation:' + source_key
            source = o.source(db, owner, item_key)
            run = {'enrollment_id': owner, 'item_key': item_key, 'source_key': source_key, 'source_json': json.dumps(source)}
            facts = o._coordinator_facts(db, run, term)
            suggested = o.suggested(db, owner, item_key) if source_key else None
            policy = o.support.term_policy(db, term['id'])
            commitments = [i for i in o.support._items(db, owner) if i['kind'] == 'followup']
            selected = next((i for i in commitments if i['source_key'] == source_key), None)
            ids = {m['id'] for m in source.get('messages', [])}
            reply_thread = ''
            for message in reversed(source.get('messages', []) if source_key else []):
                if message['author_role'] != 'student':  # in a coordinator's source, `student` is the coordinator
                    continue
                row = db.execute('SELECT channel,thread_key FROM comm_messages WHERE id=?', (message['id'],)).fetchone()
                if row and row['channel'] == 'gmail':
                    reply_thread = row['thread_key']
                break
            person = o.support._coordinator_people(db, {owner})[owner]
        path = '/api/communications/coordinators/' + term_id
        status, record, _ = o.assistant.transport.get(path)
        if status != 200:
            raise Conflict('ASSISTANT: the communications record could not be read.')
        entries = record.get('entries', [])
        return {'enrollment_id': owner, 'term_id': term_id, 'source_key': source_key, 'coordinator': person, 'policies': policy,
                'facts': facts, 'followup': selected, **({'suggested_actions': suggested} if suggested else {}),
                'reply': {'addresses': [who['email']] if who else [], 'thread_id': reply_thread},
                'commitments': commitments, 'selected_messages': [e for e in entries if e.get('message_id') in ids][:20],
                'communications': entries[:40], 'more_communications': path,
                'note': 'This conversation is with the course coordinator, not a student. The term policies are the instructor’s '
                        'rules for students: background, never text to send.'}

    def read(self, enrollment, source_key=''):
        o = self.owner
        from course_coordinators.service import owner_term
        if owner_term(enrollment):
            return self._read_coordinator(enrollment, owner_term(enrollment), source_key)
        with connect(o.path) as db:
            term = db.execute('SELECT t.* FROM course_terms t JOIN student_enrollments e ON e.term_id=t.id WHERE e.id=?', (enrollment,)).fetchone()
            if not term:
                raise NotFound('That student enrollment was not found.')
            threads = conversations(db, enrollment)
            if source_key:
                source_key = conversation_of(db, source_key, enrollment)
                if (enrollment, source_key) not in threads:
                    raise NotFound('That conversation does not belong to this student.')
            elif threads:
                source_key = max(threads, key=lambda k: threads[k]['latest'])[1]
            item_key = 'conversation:' + source_key
            joined = [{'source_key': key, 'title': part['title'], 'started': part['started'][0]}
                      for key, part in sorted((threads.get((enrollment, source_key)) or {}).get('parts', {}).items())]
            source = o.source(db, enrollment, item_key)
            run = {'enrollment_id': enrollment, 'item_key': item_key, 'source_key': source_key,
                   'source_json': json.dumps(source)}
            facts = o._record_facts(db, run, term)
            suggested = o.suggested(db, enrollment, item_key) if source_key else None  # REDO FROM CHAT
            policy = o.support.term_policy(db, term['id'])
            commitments = [i for i in o.support._items(db, enrollment) if i['kind'] == 'followup']
            # SAME MATTER: a follow-up saved under a conversation since joined to this one is this conversation's follow-up, as
            # on the Follow-up page (records keep the key they were saved with; the list keeps one follow-up per conversation).
            read_as = joins(db, {(enrollment, i['source_key']) for i in commitments if i['source_key']})
            selected = next((i for i in commitments if i['source_key'] == source_key), None) or next(
                (i for i in commitments if i['source_key'] and read_as.get((enrollment, i['source_key'])) == source_key), None)
            ids = {m['id'] for m in source.get('messages', [])}
            reply_thread = ''
            if source_key:
                for message in reversed(source.get('messages', [])):
                    if message['author_role'] != 'student':
                        continue
                    row = db.execute('SELECT m.channel,m.thread_key,r.context_json FROM comm_messages m JOIN comm_revisions r ON r.message_id=m.id WHERE m.id=? ORDER BY r.revision DESC LIMIT 1', (message['id'],)).fetchone()
                    if row and row['channel'] == 'gmail' and not json.loads(row['context_json']).get('canvas_notice'):
                        reply_thread = row['thread_key']
                    break
        path = '/api/communications/students/' + enrollment
        status, record, _ = o.assistant.transport.get(path)
        if status != 200:
            raise Conflict('ASSISTANT: the communications record could not be read.')
        _, people = o.assistant.app.extensions['gmail_mail']._students(fresh=True)
        addresses = sorted({p['address'] for p in people if p['entry']['enrollment_id'] == enrollment and not p['entry']['dropped']})
        entries = record.get('entries', [])
        notes_status, notes, _ = o.assistant.transport.get('/api/student-notes/students/' + enrollment)
        grade_status, gradebook, _ = o.assistant.transport.get('/api/gradebook/terms/' + term['id'])
        if notes_status != 200 or grade_status != 200:
            raise Conflict('ASSISTANT: the student notes or gradebook could not be read.')
        students = [s for s in gradebook.get('students', []) if s['enrollment_id'] == enrollment]
        keys = {s['row'] for s in students}
        grades = {'setup': gradebook.get('setup'), 'students': students,
                  'cells': {col: {key: cell for key, cell in cells.items() if key in keys} for col, cells in gradebook.get('cells', {}).items()}}
        return {'enrollment_id': enrollment, 'term_id': term['id'], 'source_key': source_key,
                'student': o.support.people().get(enrollment), 'policies': policy, 'facts': facts,
                'notes': notes, 'grades': grades,
                'followup': selected, **({'suggested_actions': suggested} if suggested else {}),
                # CONNECT A MESSAGE: the conversations read as part of this one (SAME MATTER), by the key Keep separate names.
                **({'joined': joined} if joined else {}),
                'reply': {'addresses': addresses, 'thread_id': reply_thread},
                'commitments': commitments, 'selected_messages': [e for e in entries if e.get('message_id') in ids][:20],
                'communications': entries[:40],
                'more_communications': path, 'note': 'Private context for the instructor. Never quote private notes or policies in student replies.'}

    def fingerprints(self, db, run, window=None, memo=None):
        """Foreign revision tokens: this plan's own writes never stale its neighbors. `window` is the first day of the calendar
        window to read (the one recorded when the draft was prepared); without it, today's. `memo` keeps calendar reads for one
        listing (PERFORMANCE: memos last one read)."""
        o, enrollment = self.owner, run['enrollment_id']
        source = json.loads(run['source_json'])
        term = source['term_id']
        requests = {a['body'].get('request_id') for a in o.target_actions(db, run)
                    if a['status'] in ('succeeded', 'approved')}
        def mine(request):
            return bool(request) and (request in requests or request.split('#')[0] in requests)
        def foreign(rows):
            kept = []
            for row in rows:
                row = dict(row)
                if mine(row.pop('request_id', None)):
                    continue
                kept.append(row)
            return digest(kept)
        values = {'v': VERSION}
        values['policies'] = foreign(db.execute('SELECT revision,request_id FROM student_support_term_policies WHERE term_id=? ORDER BY revision', (term,)))
        values['notes'] = foreign(db.execute('SELECT r.id,r.request_id FROM student_note_revisions r JOIN student_notes n ON n.id=r.note_id WHERE n.enrollment_id=? ORDER BY r.id', (enrollment,)))
        absence = list(db.execute('SELECT r.id,r.request_id FROM absence_revisions r JOIN absences a ON a.id=r.absence_id WHERE a.enrollment_id=? ORDER BY r.id', (enrollment,)))
        documents = list(db.execute('SELECT d.id,d.request_id FROM absence_documents d JOIN absences a ON a.id=d.absence_id WHERE a.enrollment_id=? ORDER BY d.id', (enrollment,)))
        changes = list(db.execute('SELECT e.id,e.request_id FROM absence_document_events e JOIN absence_documents d ON d.id=e.document_id JOIN absences a ON a.id=d.absence_id WHERE a.enrollment_id=? ORDER BY e.id', (enrollment,)))
        values['absences'] = digest([foreign(absence), foreign(documents), foreign(changes)])
        # The decision on each absence (excused, not excused, withdrawn) as others last set it; one still waiting for a decision,
        # a new one included, is no decision (its other changes are `absences`, a warning).
        decided = {}
        for row in db.execute('''SELECT r.absence_id,r.status,r.request_id FROM absence_revisions r JOIN absences a ON a.id=r.absence_id
                WHERE a.enrollment_id=? ORDER BY r.absence_id,r.revision''', (enrollment,)):
            if not mine(row['request_id']):
                decided[row['absence_id']] = row['status']
        values['decisions'] = digest(sorted((k, v) for k, v in decided.items() if v != 'pending'))
        setups = list(db.execute('SELECT id,request_id FROM gradebook_setups WHERE term_id=? ORDER BY id', (term,)))
        scores = list(db.execute('SELECT s.id,r.request_id FROM gradebook_scores s JOIN gradebook_requests r ON r.id=s.request_ref WHERE s.enrollment_id=? ORDER BY s.id', (enrollment,)))
        values['grades'] = digest([foreign(setups), foreign(scores)])
        own = o.own(db, enrollment, run['item_key'])
        messages = db.execute('''SELECT DISTINCT m.id,m.channel,m.external_id,r.revision,r.content_sha256,r.sent_at,r.author_role,r.body_text
            FROM comm_messages m JOIN comm_revisions r ON r.message_id=m.id AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=m.id)
            JOIN comm_links l ON l.message_id=m.id WHERE l.enrollment_id=? ORDER BY m.id''', (enrollment,))
        values['messages'] = digest([(r['id'], r['revision'], r['content_sha256']) for r in messages
                                     if not (r['author_role'] == 'instructor' and o._own_message(r, own['sends']))])
        accommodations = list(db.execute('SELECT section_id,revision_id FROM student_accommodations WHERE enrollment_id=? ORDER BY section_id', (enrollment,)))
        bookings = list(db.execute('SELECT id,revision_id FROM dr_exam_bookings WHERE enrollment_id=? ORDER BY id', (enrollment,)))
        values['accommodations'] = digest([[tuple(r) for r in accommodations], [tuple(r) for r in bookings]])
        # Use the exam owner's collection projection: Inbox-deleted material is
        # excluded by PRESENT, just as on the exam page (CERTIFIED-ARCHIVE / INBOX).
        # Only the papers that arrived count: a new exam for the student, or another scan of one, changes nothing a reply says.
        exams = o.assistant.app.extensions.get('exams')
        papers = []
        if exams:
            from absences.makeups import RECEIVED
            for row in db.execute('SELECT event_id,section_id FROM exam_participants WHERE enrollment_id=? ORDER BY event_id,section_id', (enrollment,)):
                event = exams._event(db, row['event_id'])
                person = exams._participant_paper(db, event, row['section_id'], enrollment)
                if person and person['collection'].get('status') in RECEIVED:
                    papers.append([row['event_id'], row['section_id']])
        values['papers'] = digest(papers)
        values['schedule'] = digest([foreign(db.execute('SELECT revision,request_id FROM scheduling_revisions WHERE term_id=? ORDER BY revision', (term,))),
                                     foreign(db.execute('SELECT id,request_id FROM scheduling_uses WHERE term_id=? ORDER BY created_at,id', (term,))),
                                     foreign(db.execute('SELECT revision,request_id FROM student_support_meeting_hours WHERE term_id=? ORDER BY revision', (term,)))])
        # SCHEDULING "CLASS SCHEDULE": the term's planned rows, class-day topics and the records its rows follow, which a follow-up's
        # facts read. A key of its own: a draft recorded before it existed carries none, so it is not outdated by it (`changes`
        # compares only the keys a record has).
        values['class_schedule'] = digest([
            foreign(db.execute('SELECT revision,request_id FROM scheduling_plan_revisions WHERE term_id=? ORDER BY revision', (term,))),
            foreign(db.execute('SELECT id,request_id FROM scheduling_plan_links WHERE term_id=? ORDER BY created_at,id', (term,)))])
        # A fixed observation window avoids heartbeat/timestamp churn. Only the calendar's
        # scheduling fields count; the normal read never contacts Google. A check reads the
        # draft's own window, so a draft approved the next campus day is not stale for that alone.
        today = date.fromisoformat(window) if window else campus_today()
        path = '/api/calendar?' + urlencode({'start': today.isoformat(), 'end': (today + timedelta(days=21)).isoformat()})
        if memo is not None and path in memo:
            status, calendar = memo[path]
        else:
            status, calendar, _ = o.assistant.transport.get(path)
            if memo is not None:
                memo[path] = (status, calendar)
        if status != 200:
            values['calendar'] = 'unavailable'
        else:
            from absences.makeup_slots import shareable
            from scheduling.engine import span
            from scheduling.clock import parse_clock
            fields = ('id', 'date', 'end_date', 'time', 'end_time', 'all_day', 'title', 'location', 'source')
            # Each event: [what it is, its day, its start and end minutes (None untimed: an all-day entry never takes a time),
            # whether a makeup may share its time].
            events = {}
            for event in calendar.get('events', []):
                times = span(event) or (None, None)
                end_date = event.get('end_date') or event.get('date')
                multiple = end_date and event.get('date') and end_date > event['date']
                if multiple and times[0] is not None:
                    end = parse_clock(event.get('end_time'))
                    times = (times[0], end if end is not None else WHOLE_DAY[1])
                events[event['id']] = [digest([event.get(k) for k in fields]), event.get('date'), *times, shareable(event)]
                if multiple:
                    # Enrollment-owned freshness metadata: older five-field events remain readable. Keep the last date so
                    # a calendar change during a multi-day entry also guards a later appointment (ASSISTANT CONTEXT).
                    events[event['id']].append(end_date)
            values['calendar'] = {'window': today.isoformat(), 'events': events}
        return values

    def intervals(self, db, run):
        """The times this plan books (a meeting, makeup sitting or calendar entry of its own) or names (the days and clock times of
        its messages, read in code: JEV DATES) as (day, start, end) minutes. A named day without a clock time is the whole day;
        with times it is from the earliest to an hour after the latest. Declined steps name nothing (PLAN COMPLETION)."""
        from jev import dates
        from scheduling.engine import span
        o, found = self.owner, set()
        for action in o.plan_actions(db, run):
            if not o.in_plan(db, action):
                continue
            body = action['body'] or {}
            booked = o._meeting(action, o.assistant.transport)
            if booked is None and action['endpoint'] in ('workspace_calendar.create', 'workspace_calendar.update') and body.get('date'):
                booked = body
            if booked:
                times = span({'time': booked.get('time'), 'end_time': booked.get('end_time') or '', 'all_day': bool(booked.get('all_day'))})
                found.add((str(booked['date']), *(times or WHOLE_DAY)))
            elif action['endpoint'] == 'gmail_mail.send':
                text = ' '.join(str(body.get(k) or '') for k in ('subject', 'body'))
                days = {d for m in dates.mentions(text, action['created_at']) for d in m['dates']}
                clocks = sorted(int(t[:2]) * 60 + int(t[3:]) for m in dates.times(text) for t in m['times'])
                for day in days:
                    found.add((day, clocks[0], min(clocks[-1] + 60, WHOLE_DAY[1])) if clocks else (day, *WHOLE_DAY))
        return found

    @staticmethod
    def _at(event, spans):
        """A kept calendar event (v2) takes a time this plan books or names."""
        if not isinstance(event, list) or event[2] is None:
            return False
        last = event[5] if len(event) > 5 else event[1]
        return any(event[1] <= day <= last and start < (event[3] if day == last else WHOLE_DAY[1])
                   and (event[2] if day == event[1] else 0) < end for day, start, end in spans)

    def calendar_changes(self, db, run, before, current, observed_at, spans=None):
        """Whether the calendar changed at a time this plan books or names (`spans`, from `intervals`): an entry there added,
        moved, edited or removed by anyone but the plan itself. A makeup only added there shares the time (ABSENCES SHAREABLE).
        Without `spans` (a v1 record, which kept no times) every change counts."""
        if not isinstance(before, dict) or not isinstance(current, dict):
            return current == 'unavailable' or before != current
        old, new = dict(before['events']), dict(current['events'])
        actions = [a for a in self.owner.target_actions(db, run)
                   if a['status'] == 'succeeded' and a['created_at'] >= observed_at]
        # An exact owner revision produced by this plan is expected. A later
        # manual edit of that same appointment/event must still invalidate it.
        for action in actions:
            result = action['events'][-1].get('detail', {}).get('result') or {}
            key, revision, actual = None, None, None
            if action['endpoint'] in ('student_support.create', 'student_support.revise'):
                receipt = db.execute('SELECT item_id,revision FROM student_support_requests WHERE request_id=?',
                                     (action['body'].get('request_id'),)).fetchone()
                if receipt:
                    kind = db.execute('SELECT kind FROM student_support_items WHERE id=?', (receipt['item_id'],)).fetchone()
                    if kind and kind[0] == 'appointment':
                        key, revision = 'office-hours:' + receipt['item_id'], receipt['revision']
                        actual = db.execute('SELECT MAX(revision) FROM student_support_revisions WHERE item_id=?', (receipt['item_id'],)).fetchone()[0]
            elif action['endpoint'] in ('workspace_calendar.create', 'workspace_calendar.update', 'workspace_calendar.delete'):
                if result.get('custom_id') and result.get('version'):
                    key, revision = 'custom:' + result['custom_id'], result['version']
                    row = db.execute('SELECT version FROM calendar_events WHERE id=?', (result['custom_id'],)).fetchone()
                    actual = row[0] if row else None
            if key:
                # A later successful step in the same plan may revise it again.
                expected = [a['events'][-1].get('detail', {}).get('result') or {} for a in actions]
                later = any(r.get('id') == result.get('id') and r.get('revision', r.get('version', 0)) == actual for r in expected)
                if actual != revision and not later:
                    return True
                old.pop(key, None)
                new.pop(key, None)
        term = json.loads(run['source_json'])['term_id']
        requests = {a['body'].get('request_id') for a in actions}
        setup = db.execute('SELECT request_id FROM gradebook_setups WHERE term_id=? ORDER BY revision DESC LIMIT 1', (term,)).fetchone()
        if setup and setup[0] in requests:
            for row in db.execute('SELECT setup_json FROM gradebook_setups WHERE term_id=?', (term,)):
                for column in json.loads(row[0]).get('items', []):
                    old.pop('gradebook:' + column['id'], None)
                    new.pop('gradebook:' + column['id'], None)
        # An exam this plan wrote to (a makeup sitting, the student seated in it: EXAMS WITH PARTICIPANTS) changes its sittings'
        # calendar rows, a makeup's length included (MAKEUP LENGTH). They are the plan's own while nothing in that exam changed
        # after its last write there: each submission's guard has already compared everything up to that write.
        written = {}
        for action in actions:
            parts = action['path'].split('?')[0].split('/')
            if action['endpoint'].startswith('exams.') and len(parts) > 3 and parts[1:3] == ['api', 'exams']:
                written[parts[3]] = max(written.get(parts[3], ''), utc(action['events'][-1]['at']))
        for event_id, last in written.items():
            since = db.execute('''SELECT MAX(at) FROM (SELECT updated_at at FROM exam_events WHERE id=?
                UNION ALL SELECT updated_at FROM exam_sittings WHERE event_id=? UNION ALL SELECT updated_at FROM exam_participants WHERE event_id=?)''',
                               (event_id, event_id, event_id)).fetchone()[0]
            if since and utc(since) > last:
                continue  # changed after the plan's write: compared as anyone's change
            for (sitting,) in db.execute('SELECT id FROM exam_sittings WHERE event_id=?', (event_id,)):
                old.pop('exam:' + sitting, None)
                new.pop('exam:' + sitting, None)
        # The plan's own bookings in an exam stay where it put them, as its own appointments must: its makeup sitting at the day
        # and time it booked (unless the plan itself moved it later), the student in the sitting it seated them in.
        moved = {action['path'].split('/')[3] for action in actions if action['endpoint'] == 'exams.change_sitting'}
        for action in actions:
            parts, body = action['path'].split('?')[0].split('/'), action['body'] or {}
            if action['endpoint'] == 'exams.sitting' and body.get('date') and parts[3] not in moved:
                if not db.execute('SELECT 1 FROM exam_sittings WHERE event_id=? AND date=? AND time=?',
                                  (parts[3], body['date'], body.get('time') or '')).fetchone():
                    return True
            elif action['endpoint'] == 'exams.participant' and body.get('sitting_id') and len(parts) > 6:
                seat = db.execute('SELECT sitting_id FROM exam_participants WHERE event_id=? AND section_id=? AND enrollment_id=?',
                                  (parts[3], parts[5], parts[6])).fetchone()
                if not seat or seat[0] != body['sitting_id']:
                    return True
        if spans is None:
            return before['window'] != current['window'] or old != new
        sign = lambda event: event[0] if isinstance(event, list) else event
        changed = [key for key in old.keys() | new.keys() if sign(old.get(key)) != sign(new.get(key))]
        return any(self._at(old.get(key), spans) or self._at(new.get(key), spans) and not (key not in old and new[key][4])
                   for key in changed)

    def remember(self, db, run):
        values = self.fingerprints(db, run)
        db.execute('INSERT INTO student_support_context_checks(run_id,fingerprints_json,created_at) VALUES(?,?,?)',
                   (run['id'], json.dumps(values), timestamp()))
        return values

    def changes(self, db, run, memo=None):
        """What changed since the draft was prepared: {'refuse': [...], 'warn': [...]} labels (BLOCKING refuses; a v1 record's
        changes are all warnings, since it kept no times to tell)."""
        row = db.execute('SELECT fingerprints_json,created_at FROM student_support_context_checks WHERE run_id=? ORDER BY id DESC LIMIT 1', (run['id'],)).fetchone()
        if not row:
            # No timestamp comparison can establish what an old model saw.
            return {'refuse': ['source context (this draft predates freshness checks)'], 'warn': []}
        before = json.loads(row[0])
        calendar = before.get('calendar')
        current = self.fingerprints(db, run, calendar.get('window') if isinstance(calendar, dict) else None, memo)
        sure = before.get('v') == VERSION
        spans = self.intervals(db, run) if sure else None
        found = {'refuse': [], 'warn': []}
        for key, value in before.items():
            if key not in LABELS:
                continue
            moved = (self.calendar_changes(db, run, value, current.get(key), row['created_at'], spans) if key == 'calendar'
                     else current.get(key) != value)
            if moved:
                found['refuse' if sure and key in BLOCKING else 'warn'].append(LABELS[key])
        return found

    def guard(self, db, run):
        refused = self.changes(db, run)['refuse']
        if refused:
            raise Conflict('ASSISTANT: ' + ', '.join(refused).capitalize() + ' changed after this draft was prepared. Retry suggestions to review a fresh plan; nothing was submitted.')
