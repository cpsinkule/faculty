"""TRIAGE CALIBRATION (requirement, 2026-10-08).
Every student message of the current term, read as triage would have read its conversation when it was the latest (`_state`,
the shape `snapshots` builds), is asked of both models with the live QUESTIONS and RULE. What the instructor did next is the label:

- visible (must never become FYI): you replied next; it is an absence's notice; or its follow-up was To do before the student
  wrote again;
- fyi_ok (may become FYI): resolved by you, with no reply, before the student wrote again (medium), or never answered and
  never tracked (weak);
- chips: absence (an absence's notice), grading (a Gradescope regrade request).

Wellbeing and urgency have no real labels: the invented contract set below gives them (`calibrate.py` reports how often each
model flags real messages too). Nothing here writes a record; outputs hold ids, labels and probabilities, never text."""
import json
import re
from datetime import datetime

from communications.record import utc
from jev.checks import CAMPUS, relative_dates
from student_support.triage import StudentTriage, upcoming

from .. import labels
from ..cases import Case, label
from .followups import _conversation, _m, _triage_run, _triage_state

CHECK = 'triage_calibration'


def _state(db, enrollment, key, thread, i):
    """Triage's state (the shape `snapshots` builds) for the conversation when message `i` was its latest, from the conversation's
    own messages. Not the as-of copy: messages captured before live capture began (2026-09-30) share one capture time, so an
    as-of read at that time already holds the later replies (measured: 64 of 292 cases ended on another message)."""
    upto = thread[:i + 1]
    rows = [(m, db.execute('''SELECT r.subject,r.body_text,r.content_sha256,r.sent_at,r.author_role,r.context_json,c.external_id,c.channel
        FROM comm_revisions r JOIN comm_messages c ON c.id=r.message_id WHERE r.message_id=? AND r.revision=?''',
                           (m['id'], m['revision'])).fetchone()) for m in upto[-6:]]
    state = {'context_incomplete': len(upto) > 6 or any(len(r['body_text'] or '') > 8000 for _, r in rows),
             'messages': [{'id': m['id'], 'revision': m['revision'], 'sha256': r['content_sha256'], 'role': r['author_role'],
                           'sent_at': r['sent_at'], 'subject': r['subject'], 'text': (r['body_text'] or '')[:8000]} for m, r in rows]}
    _, latest = rows[-1]
    if latest['channel'] == 'gmail':
        context = json.loads(latest['context_json'] or '{}')
        thread_id = context.get('thread_id') or (context.get('gmail') or {}).get('thread_id')
        if isinstance(thread_id, str) and re.fullmatch(r'[0-9a-f]{1,32}', thread_id) and re.fullmatch(r'[0-9a-f]{1,32}', latest['external_id']):
            state['mail'] = {'thread_id': thread_id, 'message_id': latest['external_id']}
    moment = datetime.fromisoformat(utc(latest['sent_at']).replace('Z', '+00:00')).astimezone(CAMPUS)
    state['relative_dates_resolved_by_code'] = relative_dates({'sent_at_campus': moment.isoformat(),
                                                               'email_text': (latest['body_text'] or '')[:8000]})
    term = db.execute('SELECT term_id FROM comm_links WHERE message_id=? AND enrollment_id=? LIMIT 1', (thread[i]['id'], enrollment)).fetchone()
    state['upcoming'] = upcoming(db, {(enrollment, key): (term[0], moment)}).get((enrollment, key), []) if term else []
    return state


def _p(run):
    """The sandbox triage runner, plus what the gates read: the kind's top probability and both kinds' probabilities."""
    def wrapped(service):
        result = run(service)
        receipt = service.store._entries.get(service.used[-1]) if service.used else None
        answers = (receipt or {}).get('answers') or {}
        kind = answers.get('kind') or {}
        if kind:
            result['p'].update(kind_top=kind['probabilities'][kind['choice']],
                               **{f'kind_{k}': v for k, v in kind['probabilities'].items()})
            result['outcome']['kind_choice'] = kind['choice']
            result['p']['urgency_confidence'] = (answers.get('urgency') or {}).get('confidence')
        return result
    return wrapped


def real(ctx):
    db, cases = ctx.db, []
    notices = {row[0] for row in db.execute('SELECT DISTINCT notice_message_id FROM absence_revisions WHERE notice_message_id IS NOT NULL')}
    followups = {}
    for item in db.execute("SELECT id,enrollment_id,source_key FROM student_support_items WHERE kind='followup' ORDER BY created_at"):
        followups.setdefault((item['enrollment_id'], _conversation(db, item['enrollment_id'], item['source_key'])), []).append(item['id'])
    students = [row[0] for row in db.execute('''SELECT DISTINCT l.enrollment_id FROM comm_links l JOIN course_terms t ON t.id=l.term_id
        WHERE t.is_current=1 AND l.role='author' AND l.enrollment_id IS NOT NULL ORDER BY l.enrollment_id''')]
    for enrollment in students:
        for key, thread in labels.timeline(db, enrollment).items():
            history = [r for item in followups.get((enrollment, key), []) for r in labels.revisions(db, item)]
            history.sort(key=lambda r: r['created_at'])
            for i, m in enumerate(thread):
                if m['role'] != 'student':
                    continue
                nxt = thread[i + 1] if i + 1 < len(thread) else None
                if nxt and nxt['role'] == 'student':
                    continue  # triage reads a conversation where the student wrote last: the later message is the case
                later = next((x for x in thread[i + 1:] if x['role'] == 'student'), None)
                decided = labels.between(history, m['captured_at'], later['captured_at'] if later else None)
                wanted = {}
                if m['id'] in notices:
                    wanted['visible'] = label(1, 'strong')
                    wanted['absence'] = label(1, 'strong')
                elif nxt and nxt['role'] == 'instructor':
                    wanted['visible'] = label(1, 'medium')
                elif decided and decided[-1]['state'] == 'todo':
                    wanted['visible'] = label(1, labels.strength(decided[-1]['actor']))
                elif decided and decided[-1]['state'] == 'resolved' and decided[-1]['actor'] == 'workspace':
                    wanted['visible'] = label(0, 'medium')
                elif not history and nxt is None:
                    wanted['visible'] = label(0, 'weak')
                if m['channel'] == 'gradescope_regrade':
                    wanted['grading'] = label(1, 'strong')
                state = _state(db, enrollment, key, thread, i)
                if state['messages'][-1]['role'] != 'student':
                    continue
                cases.append(Case(CHECK, f"real-{enrollment[:8]}-{m['id'][:8]}", 'real',
                                  _p(_triage_run(StudentTriage.owner(enrollment, key), state)), wanted,
                                  {'enrollment': enrollment, 'conversation': key, 'message': m['id'], 'moment': m['captured_at'],
                                   'context_incomplete': state['context_incomplete'], 'channel': m['channel']}))
    return cases


# The wellbeing and urgency contract set (JEV URGENCY/WELLBEING): a concern must be caught; an ordinary sickness or worry must not
# pin. Written for this calibration; the sandbox's own triage set (`followups.invented`) is added to it.
WELLBEING = [
    ('grief_mother', 'My mom passed away on Sunday and I am going home for the funeral this week. I will miss Thursday\'s quiz.', 1),
    ('hospital_stay', 'I have been in the hospital since Tuesday with appendicitis and had surgery yesterday. I will be out at least a week.', 1),
    ('panic', 'I had a panic attack during the last exam and had to leave. It keeps happening and I don\'t know how to handle the next one.', 1),
    ('self_harm', 'Sometimes I think everyone would be better off without me. I haven\'t been to class because I can\'t get out of bed.', 1),
    ('assault', 'Something happened to me at a party last weekend and I haven\'t been able to focus or come to class. I\'m talking to '
                'someone at the counseling center.', 1),
    ('eviction', 'I was evicted from my apartment and I\'ve been sleeping in my car, so I missed the quiz. I\'m trying to find a place.', 1),
    ('family_emergency', 'My little brother was in a serious accident and is in the ICU. I\'m at the hospital with my family and will '
                         'miss class.', 1),
    ('chronic_flare', 'My Crohn\'s disease has flared up badly and my doctor put me on bed rest for two weeks.', 1),
    ('depression', 'I\'ve been struggling with depression this month and fell behind on everything. My counselor suggested I reach out.', 1),
    ('concussion', 'I got a concussion at practice and the trainer says no screens for a week, so I can\'t do the homework.', 1),
    ('unsafe', 'My roommate threatened me last night and I don\'t feel safe going back to my dorm. I missed class this morning.', 1),
    ('miscarriage', 'I had a miscarriage over the weekend and I need a few days. I\'m sorry to miss Monday\'s class.', 1),
    ('cold', 'I have a bad cold and a sore throat, so I\'ll stay home from class tomorrow to avoid getting anyone sick.', 0),
    ('fever', 'I woke up with a fever this morning and missed class. Could you tell me what we covered?', 0),
    ('migraine', 'I had a migraine during lecture today and left early. Are the slides posted?', 0),
    ('stomach_bug', 'I had a stomach bug yesterday and couldn\'t make it. I\'m feeling better now.', 0),
    ('exam_nerves', 'I\'m nervous about the exam on Thursday. Any tips for what to focus on?', 0),
    ('busy_week', 'I have three exams this week and I\'m a bit overwhelmed. Could I turn in the homework a day late?', 0),
    ('car_trouble', 'My car broke down on the way to campus so I missed the quiz. I have the tow receipt.', 0),
    ('overslept', 'I overslept and missed the quiz this morning. Is there any way to make it up?', 0),
    ('interview', 'I have a job interview in Houston on Friday and will miss class. Can I take the quiz early?', 0),
    ('athletics', 'I\'ll be traveling with the volleyball team next Thursday and Friday. I have the travel letter.', 0),
    ('dentist', 'I had a dentist appointment that ran late and missed recitation.', 0),
    ('grade_worry', 'I\'m worried my grade is too low after Exam 1. What can I do to improve it?', 0),
]
URGENT = [
    ('locked_out_exam', 'The exam starts in an hour and I can\'t log into the testing site. What do I do?', [{'title': 'Exam 2', 'when': 'the same day'}], 2),
    ('quiz_tomorrow_sick', 'I\'m sick and won\'t be able to take tomorrow\'s quiz. Can I take a makeup?', [{'title': 'Quiz 5', 'when': 'the next day'}], 2),
    ('next_week', 'Will next week\'s quiz cover section 3.5 too?', [{'title': 'Quiz 5', 'when': 'later the same week'}], 1),
    ('end_of_term', 'Is the final exam cumulative? Just planning ahead.', [], 0),
]


def invented():
    cases = []
    for name, text, concern in WELLBEING:
        state = _triage_state([_m('student', 7, 15, text)])
        cases.append(Case(CHECK, f'inv-wellbeing-{name}', 'invented', _p(_triage_run(f'cal:{name}', state)),
                          {'wellbeing': label(concern)}, {'set': 'wellbeing'}))
    for name, text, upcoming, level in URGENT:
        state = _triage_state([_m('student', 7, 15, text)], upcoming)
        cases.append(Case(CHECK, f'inv-urgency-{name}', 'invented', _p(_triage_run(f'cal:{name}', state)),
                          {'urgency_level': label(level), 'visible': label(1)}, {'set': 'urgency'}))
    return cases
