"""The other live Jev checks, through their own code: absence-draft verification, Assistant evidence, Ctrl K routing, Canvas
column matching, bank tagging; and use case 6's text part (does grading feedback refer to rubric wording the pinned rubric no
longer has?), asked of both models."""
from datetime import datetime
import json
import re

from absences.service import REASONS
from gradebook import canvas_import
from jev import checks as jev_checks, matching as canvas_matching, routing
from jev.service import noul
from scheduling.clock import CAMPUS_ZONE

from ..cases import UNTRUSTED, Case, label, noul_p, status

# ---------------------------------------------------------------------------------------------------------------- absence (5)
FIELDS = ('first_date', 'last_date', 'reason', 'details')


def _absence_run(owner, source, facts):
    def run(service):
        checked, verification = jev_checks.absence(service, owner, source, {**facts, 'warnings': []}, None)
        entry = service.store._entries.get(service.used[-1]) if service.used else None
        answers = (entry or {}).get('answers') or {}
        p = {name: answers[name]['noul'] for name in (*FIELDS, 'conflict') if name in answers}
        blanked = [k for k in ('first_date', 'last_date', 'reason') if facts.get(k) and not checked.get(k)]
        return {'outcome': {'status': verification.get('status'), 'unsupported': ','.join(verification.get('unsupported') or []),
                            'conflict': verification.get('conflict'), 'blanked': ','.join(blanked)}, 'p': p}
    return run


def _absence_source(sent, subject, text, attachments=()):
    return {'sent_at_campus': sent, 'subject': subject, 'email_text': text, 'attachment_names': list(attachments), 'reason_labels': REASONS}


# ---------------------------------------------------------------------------------------------------------------- evidence (6)
def _evidence_run(owner, query, answer, sources):
    def run(service):
        result = jev_checks.evidence(service, owner, query, answer, sources)
        outcome = {'status': result['status'], 'partial': result.get('partial'),
                   'irrelevant': ','.join(sorted(result.get('irrelevant_sources') or []))}
        for check in result.get('checks', []):
            outcome[f"p{check['paragraph'] - 1}"] = check['verdict']  # paragraph text is never kept
        p = {f"p{check['paragraph'] - 1}_confidence": check['confidence'] for check in result.get('checks', [])}
        return {'outcome': outcome, 'p': p}
    return run


def _source(ident, text, kind='read', fingerprint_of='text'):
    return {'id': ident, 'path': f'/api/{ident}', 'observed_at': '2026-10-07T15:00:00Z', 'text': text, 'fingerprint': ident * 4,
            'fingerprint_of': fingerprint_of, 'kind': kind, 'truncated': False}


# ---------------------------------------------------------------------------------------------------------------- palette (8)
def _palette_run(query, groups, ids):
    def run(service):
        cleaned_query, cleaned = routing.clean({'query': query, 'groups': groups})
        result = routing.route(service, cleaned_query, cleaned)
        first = (result.get('picks') or [{}])[0]
        return {'outcome': {'status': result['status'], 'pick': ids.get(first.get('id'), '') if result['status'] == 'ok' else '',
                            'confident': result.get('confident'), 'tab': first.get('tab', '')},
                'p': {'confidence': first.get('confidence')}}
    return run


# ---------------------------------------------------------------------------------------------------------------- Canvas match (9)
def _canvas_run(term_id, column, items, categories):
    def run(service):
        asked = canvas_matching.request(column, items, categories)
        if not asked:
            return {'outcome': {'status': 'not_asked'}, 'p': {}}
        state, questions, options = asked
        receipt = service.ask(canvas_matching.PURPOSE, canvas_matching._owner(term_id, column), state, questions, canvas_matching.RULE)
        hint = canvas_matching._hint(receipt, options) if receipt.get('status') == 'complete' else None
        by_id = {item['id']: item for item in items}
        suggested = ''
        if hint and hint['confidence'] >= canvas_import.JEV_CONFIDENCE and by_id.get(hint['item_id'], {}).get('points') == column.get('points'):
            suggested = hint['item_id']
        return {'outcome': {'status': status(receipt), 'suggested': suggested, 'likely': (hint or {}).get('item_id', '')},
                'p': {'confidence': (hint or {}).get('confidence')}}
    return run


# ---------------------------------------------------------------------------------------------------------------- bank tags (10)
def _bank_run(definition_id, question, outline, printed):
    from question_bank import jev_tags

    def run(service):
        placed = jev_tags.place(service, definition_id, [question], outline, printed=printed)
        found = placed.get(question['id'])
        return {'outcome': {'placed': bool(found), 'nodes': ','.join((found or {}).get('nodes') or []),
                            'difficulty': (found or {}).get('difficulty', ''), 'types': ','.join((found or {}).get('types') or [])},
                'p': {}}
    return run


# ---------------------------------------------------------------------------------------------------------------- use case 6 (text)
FEEDBACK_RULE = 'sandbox-rubric-feedback-v1'
FEEDBACK = {'refers_to_unpinned_rubric': noul(
    UNTRUSTED + 'Grading feedback is under `feedback`: each applied rubric line\'s id and the grader\'s reason, then any comment. The '
    'rubric now pinned for this question is under `pinned_rubric`. Does the feedback quote or rely on rubric wording, a requirement, '
    'a value or a line that the pinned rubric does not have (for example an older version of the rubric)? Paraphrase of a pinned line '
    'is not.', {'true': 'The feedback refers to rubric content that is not in the pinned rubric.',
                'false': 'Everything the feedback refers to is in the pinned rubric.'})}


def _feedback_run(owner, feedback, pinned):
    state = {'feedback': feedback, 'pinned_rubric': pinned}

    def run(service):
        receipt = service.ask('sandbox-rubric-feedback', owner, state, FEEDBACK, FEEDBACK_RULE)
        p = noul_p(receipt, 'refers_to_unpinned_rubric')
        return {'outcome': {'status': status(receipt), 'flagged': None if p is None else p >= .5}, 'p': {'refers_to_unpinned_rubric': p}}
    return run


def _rubric_lines(rules):
    return [{'id': item['id'], 'points': item.get('points'), 'description': item.get('description', '')} for item in rules.get('items', [])]


# ================================================================================================================ invented
def invented():
    cases = []
    sent = '2026-10-06T09:12:00-05:00'
    told = ('Hi Professor, I wasn\'t in class yesterday (Monday) because I had a fever and went to the student health center. '
            'I attached the visit note. Sorry I missed the quiz!')
    absences = [
        ('right', told, {'first_date': '2026-10-05', 'last_date': '2026-10-05', 'reason': 'illness', 'details': 'Fever; student health center visit'},
         {'first_date': 1, 'last_date': 1, 'reason': 1, 'details': 1, 'conflict': 0}),
        ('wrong_day', told, {'first_date': '2026-10-06', 'last_date': '2026-10-06', 'reason': 'illness', 'details': ''},
         {'first_date': 0, 'last_date': 0, 'reason': 1}),
        ('wrong_reason', told, {'first_date': '2026-10-05', 'last_date': '2026-10-05', 'reason': 'family', 'details': ''},
         {'first_date': 1, 'reason': 0}),
        ('invented_detail', told, {'first_date': '2026-10-05', 'last_date': '2026-10-05', 'reason': 'illness', 'details': 'Hospitalized overnight'},
         {'details': 0}),
        ('conflicting', 'I missed class on Monday, sorry, I mean Wednesday, because of a job interview. Actually it was a doctor\'s appointment.',
         {'first_date': '2026-10-05', 'last_date': '2026-10-05', 'reason': 'interview', 'details': ''}, {'conflict': 1}),
        ('range', 'I will be away Wednesday through Friday this week for a university debate tournament in Dallas.',
         {'first_date': '2026-10-07', 'last_date': '2026-10-09', 'reason': 'activity', 'details': 'Debate tournament in Dallas'},
         {'first_date': 1, 'last_date': 1, 'reason': 1, 'details': 1, 'conflict': 0}),
    ]
    for name, text, facts, wanted in absences:
        cases.append(Case('absence', 'inv-' + name, 'invented', _absence_run('sandbox:' + name, _absence_source(sent, 'Absence', text), facts),
                          {k: label(v) for k, v in wanted.items()}))

    hours = _source('calendar', 'Office hours: Tuesday and Thursday 1:00–5:00 pm, BLOC 319.')
    record = _source('absences', 'Absence on Monday, October 5, 2026: excused (illness). Absence on Wednesday, October 7: pending.')
    gradebook = _source('gradebook', 'Gradebook categories: Quizzes 20%, Exams 60%, iClicker 10%, WebAssign 10%.')
    exam = _source('exams', 'Exam 2: Thursday, October 22, 7:00–9:00 pm, BLOC 166. Makeup: Friday, October 23, 8:00 am, BLOC 309.')
    prep = _source('preparation', "Faculty's records given to this preparation: makeup slots Tuesdays 2–5 pm in BLOC 309; Quiz 4 is worth "
                   '10 points.\n\nCards this preparation proposed (exact requests): {"endpoint": "exams.sitting", "date": "2026-10-13", '
                   '"time": "14:00", "room": "BLOC 309"}', kind='preparation')
    student = _source('message', 'Student wrote on October 7: "I can only do afternoons. Can I make up Quiz 4?"')
    evidence = [
        ('office_and_absence', 'When are my office hours, and was her Monday absence excused?',
         'Your office hours are Tuesday and Thursday from 1 to 5 pm in BLOC 319.\n\nHer absence on Monday, October 5 was recorded as '
         'unexcused.\n\nShe also missed class on Friday, October 2.\n\nWould you like me to draft a reply to her?',
         [hours, record, gradebook], {'p0': 'supported', 'p1': 'contradicted', 'p2': 'unsupported', 'p3': 'not_factual'}, 'gradebook'),
        ('exam_date', 'When is Exam 2 and its makeup?',
         'Exam 2 is on Wednesday, October 21 at 7 pm in BLOC 166.\n\nThe makeup is Friday, October 23 at 8 am in BLOC 309.',
         [exam], {'p0': 'contradicted', 'p1': 'supported'}, ''),
        ('preparation', 'Prepare the follow-up for this student.',
         'I proposed booking her Quiz 4 makeup on Tuesday, October 13 at 2 pm in BLOC 309.\n\nShe asked for an afternoon time.\n\n'
         'Quiz 4 is worth 15 points.',
         [prep, student], {'p0': 'supported', 'p1': 'supported', 'p2': 'contradicted'}, ''),
    ]
    for name, query, answer, sources, wanted, irrelevant in evidence:
        labels = {k: label(v) for k, v in wanted.items()}
        labels['irrelevant'] = label(irrelevant)
        cases.append(Case('evidence', 'inv-' + name, 'invented', _evidence_run('sandbox:' + name, query, answer, sources), labels))

    groups, ids = _palette_groups()
    sentences = [
        ('open quiz 3 grading', 'Grading: Quiz 3', 'ok'), ('show me the flagged grades on exam 1', 'Grading: Exam 1', 'ok'),
        ('section 508 roster', 'Sections: MATH 140 · Fall 2026 · Section 508', 'ok'),
        ('write an email to a student', 'Actions: Write to student…', 'ok'),
        ('how many students missed quiz 2?', '', 'not_navigation'), ('BLOC 166 seat layout', 'Rooms: BLOC 166', 'ok'),
        ('record an absence for someone', 'Actions: Record an absence', 'ok'), ('the absences page', 'Pages: Absences', 'ok'),
        ('quiz 4 answer key', 'Grading: Quiz 4', 'ok'), ('print copies of quiz 5', 'Assignments: Quiz 5', 'ok'),
        ('what is the class average on exam 1', '', 'not_navigation'), ('fall gradebook weights', 'Gradebooks: MATH 140 · Fall 2026', 'ok'),
        ('canvas modules for section 507', 'Canvas: MATH 140 · Fall 2026 · Section 507', 'ok'),
        ('exam 2 makeup rooms and dates', 'Exams: Exam 2', 'ok'),
    ]
    for query, wanted, state in sentences:
        cases.append(Case('palette', 'inv-' + re.sub(r'\W+', '-', query)[:40], 'invented', _palette_run(query, groups, ids),
                          {'status': label(state), **({'pick': label(wanted)} if wanted else {})}))

    items = [{'id': 'q3', 'title': 'Quiz 3', 'points': 10, 'category_id': 'quizzes'}, {'id': 'q4', 'title': 'Quiz 4', 'points': 10, 'category_id': 'quizzes'},
             {'id': 'e1', 'title': 'Exam 1', 'points': 100, 'category_id': 'exams'}, {'id': 'e2', 'title': 'Exam 2', 'points': 100, 'category_id': 'exams'},
             {'id': 'wa42', 'title': 'WebAssign 4.2', 'points': 20, 'category_id': 'webassign'},
             {'id': 'ic', 'title': 'iClicker Week 6', 'points': 10, 'category_id': 'iclicker'}]
    categories = [{'id': 'quizzes', 'name': 'Quizzes'}, {'id': 'exams', 'name': 'Exams'}, {'id': 'webassign', 'name': 'WebAssign'},
                  {'id': 'iclicker', 'name': 'iClicker'}]
    columns = [('Quiz III', 'Quizzes', 10, 'q3'), ('Test 1 Grade', 'Exams', 100, 'e1'), ('Quiz 5', 'Quizzes', 10, ''),
               ('WA 4.2 Derivatives', 'WebAssign', 20, 'wa42'), ('Exam #2', 'Exams', 100, 'e2'), ('Final Exam', 'Exams', 150, ''),
               ('Quiz 4 makeup', 'Quizzes', 10, 'q4'), ('iClicker Wk 6', 'iClicker', 10, 'ic')]
    for title, group, points, wanted in columns:
        column = {'key': 'canvas-' + re.sub(r'\W+', '-', title), 'title': title, 'group': group, 'points': points, 'course_id': 'c1'}
        cases.append(Case('canvas_match', 'inv-' + column['key'], 'invented', _canvas_run('term-x', column, items, categories),
                          {'likely': label(wanted)}))

    pinned = [{'id': 'L1', 'points': 2, 'description': 'Finds the derivative 6x + 2 correctly.'},
              {'id': 'L2', 'points': 2, 'description': 'Sets the derivative equal to zero and solves x = -1/3.'},
              {'id': 'L3', 'points': 1, 'description': 'States the minimum value.'}]
    feedback = [
        ('paraphrase', [{'line': 'L1', 'reason': 'Derivative 6x+2 is right.'}, {'line': 'L2', 'reason': 'Solved 6x+2=0 for x=-1/3.'}], 0),
        ('deleted_line', [{'line': 'L1', 'reason': 'Correct derivative.'}, {'line': 'L4', 'reason': 'Checks the second derivative is positive, as L4 requires.'}], 1),
        ('old_value', [{'line': 'L2', 'reason': 'Sets the derivative to zero and solves x = -1/2 as the rubric expects.'}], 1),
        ('old_points', [{'line': 'L3', 'reason': 'States the minimum value, worth 3 points on this rubric.'}], 1),
        ('plain', [{'line': 'L3', 'reason': 'Gave the minimum value -1/3 at the end.'}], 0),
    ]
    for name, lines, wanted in feedback:
        cases.append(Case('rubric_feedback', 'inv-' + name, 'invented', _feedback_run('sandbox:' + name, {'lines': lines}, pinned),
                          {'refers_to_unpinned_rubric': label(wanted)}))
    return cases


def _palette_groups():
    """The palette's own kinds of places, as `static/palette.js` sends them (ids are "kind index:item index")."""
    term = 'MATH 140 · Fall 2026'
    named = {
        'Pages': ['Teaching Inbox', 'Mail', 'Absences', 'Calendar', 'Exams', 'Grading', 'Seating', 'Follow-up', 'Question bank',
                  'Workspace settings'],
        'Actions': ['Write to student…', 'Upload scans', 'Record an absence', 'Import Canvas grades', 'Switch theme'],
        'Courses': ['MATH 140'],
        'Sections': [f'{term} · Section {n}' for n in (507, 508, 514)],
        'Canvas': [f'{term} · Section {n}' for n in (507, 508, 514)],
        'Assignments': ['Quiz 2', 'Quiz 3', 'Quiz 4', 'Quiz 5', 'Exam 1', 'Exam 2'],
        'Exams': ['Exam 1', 'Exam 2', 'Final Exam'],
        'Grading': ['Quiz 2', 'Quiz 3', 'Quiz 4', 'Exam 1'],
        'Seating charts': ['Exam 2 · BLOC 166'],
        'Rooms': ['BLOC 166', 'BLOC 309', 'BLOC 319'],
        'Gradebooks': [term],
    }
    groups, ids = {}, {}
    for k, (kind, labels) in enumerate(named.items()):
        rows = []
        for i, text in enumerate(labels):
            ident = f'{k}:{i}'
            detail = term + ' · Grading' if kind == 'Grading' else ''
            rows.append({'id': ident, 'label': text, 'detail': detail})
            ids[ident] = f'{kind}: {text}'
        groups[kind] = rows
    return groups, ids


# ================================================================================================================ real
def real(ctx):
    db, cases = ctx.db, []
    # Absence (5): each notice email you recorded an absence from, with the facts of the absence's first revision, plus a perturbed
    # copy of each (a date off by a week; another reason) that the email cannot support.
    for row in db.execute('''SELECT a.absence_id,a.first_date,a.last_date,a.reason,a.details,a.notice_message_id,r.body_text,r.subject,r.sent_at,
            r.attachments_json FROM absence_revisions a JOIN comm_revisions r ON r.message_id=a.notice_message_id
            AND r.revision=(SELECT MAX(revision) FROM comm_revisions WHERE message_id=a.notice_message_id)
            WHERE a.revision=1 AND a.notice_message_id IS NOT NULL AND a.notice_message_id!='' ORDER BY a.created_at''').fetchall():
        moment = datetime.fromisoformat(row['sent_at'].replace('Z', '+00:00')).astimezone(CAMPUS_ZONE).isoformat()
        names = [f.get('name') for f in json.loads(row['attachments_json'] or '[]') if isinstance(f, dict)]
        source = _absence_source(moment, row['subject'] or '', (row['body_text'] or '')[:60000], names)
        facts = {k: row[k] or '' for k in FIELDS}
        meta = {'absence': row['absence_id'], 'message': row['notice_message_id']}
        cases.append(Case('absence', 'real-' + row['absence_id'][:12], 'real', _absence_run('sandbox:' + row['absence_id'], source, facts),
                          {'first_date': label(1, 'medium'), 'reason': label(1, 'medium')}, meta))
        if facts['first_date']:
            from datetime import date, timedelta
            shifted = (date.fromisoformat(facts['first_date']) + timedelta(days=7)).isoformat()
            wrong = {**facts, 'first_date': shifted, 'last_date': shifted, 'details': ''}
            cases.append(Case('absence', 'real-' + row['absence_id'][:12] + '-week', 'real', _absence_run('sandbox:' + row['absence_id'] + ':w', source, wrong),
                              {'first_date': label(0, 'silver')}, {**meta, 'perturbed': 'first_date+7'}))
        other = next(k for k in REASONS if k != facts['reason'])
        cases.append(Case('absence', 'real-' + row['absence_id'][:12] + '-reason', 'real',
                          _absence_run('sandbox:' + row['absence_id'] + ':r', source, {**facts, 'reason': other, 'details': ''}),
                          {'reason': label(0, 'silver')}, {**meta, 'perturbed': 'reason'}))
        if sum(1 for c in cases if c.check == 'absence') >= 45:
            break

    # Canvas match (9): the current gradebook columns, matched against Canvas-like titles made from them in code (a numeral
    # written another way should match; a number that is not there should not).
    for term_id, setup in db.execute('''SELECT g.term_id,g.setup_json FROM gradebook_setups g JOIN course_terms t ON t.id=g.term_id
            WHERE t.is_current=1 AND g.revision=(SELECT MAX(revision) FROM gradebook_setups WHERE term_id=g.term_id)''').fetchall():
        setup = json.loads(setup)
        items = [i for i in setup.get('items', []) if not i.get('archived') and i.get('title')]
        categories = setup.get('categories', [])
        names = {c['id']: c['name'] for c in categories}
        made = 0
        for item in items:
            match = re.fullmatch(r'(Quiz|Exam|Test)\s*(\d+)', item['title'].strip())
            if not match or made >= 12:
                continue
            roman = {'1': 'I', '2': 'II', '3': 'III', '4': 'IV', '5': 'V', '6': 'VI', '7': 'VII', '8': 'VIII', '9': 'IX', '10': 'X'}
            kind, number = match.groups()
            group = names.get(item.get('category_id'), '')
            for title, wanted in ((f'{kind} {roman.get(number, number)}', item['id']), (f'{kind} #{number} Grade', item['id']),
                                  (f'{kind} {int(number) + 40}', '')):
                column = {'key': 'canvas-' + re.sub(r'\W+', '-', title), 'title': title, 'group': group, 'points': item.get('points'), 'course_id': 'c'}
                cases.append(Case('canvas_match', f'real-{term_id[:6]}-{column["key"]}', 'real', _canvas_run(term_id, column, items, categories),
                                  {'likely': label(wanted, 'silver')}, {'term': term_id, 'item': item['id']}))
            made += 1

    # Bank tags (10): questions of tagging runs; the model's tags made before Jev joined (2026-10-03T18:09Z) are the reference.
    from question_bank.service import QuestionBankService
    from question_bank import content as contents
    bank = object.__new__(QuestionBankService)
    bank.path = ctx.copy
    tasks = db.execute("SELECT id,definition_id FROM question_bank_runs WHERE kind='tag' ORDER BY created_at").fetchall()
    taken = 0
    for task in tasks:
        try:
            code, outline, questions = bank._tag_inputs(dict(task))
        except Exception:  # noqa: BLE001 - a run the copy cannot rebuild is skipped
            continue
        for question in questions:
            if taken >= 30:
                break
            entry = contents.printed_entry((question.get('source') or {}).get('section'), outline)
            reference = db.execute('''SELECT node_id FROM question_bank_classifications WHERE question_id=? AND created_at<'2026-10-03T18:09'
                ORDER BY rowid DESC LIMIT 1''', (question['id'],)).fetchone() if _has(db, 'question_bank_classifications', 'node_id') else None
            wanted = {'nodes': label(reference[0], 'weak')} if reference else {}
            cases.append(Case('bank_tag', f"real-{question['id'][:12]}", 'real',
                              _bank_run(task['definition_id'], question, outline, {question['id']: entry} if entry else {}), wanted,
                              {'question': question['id'], 'run': task['id']}))
            taken += 1

    # Use case 6 (text): grades made under an earlier rubric revision, read against the rubric now pinned; their current re-grade
    # under the pinned rubric is the control.
    rows = db.execute('''SELECT res.id result_id,res.applications_json,t.rubric_id old_rubric,aq.rubric_id pinned_rubric,resp.id response_id,
            resp.current_result_id FROM frq_results res JOIN frq_tasks t ON t.id=res.task_id JOIN frq_responses resp ON resp.id=res.response_id
            JOIN frq_assignment_questions aq ON aq.assignment_id=resp.assignment_id AND aq.question_id=resp.question_id
            WHERE t.rubric_id!=aq.rubric_id ORDER BY res.created_at''').fetchall()
    seen_rubrics, taken = {}, 0
    for row in rows:
        if taken >= 15 or seen_rubrics.get(row['pinned_rubric'], 0) >= 4:
            continue
        pinned = db.execute('SELECT rules_json FROM frq_rubric_revisions WHERE id=?', (row['pinned_rubric'],)).fetchone()
        old = db.execute('SELECT rules_json FROM frq_rubric_revisions WHERE id=?', (row['old_rubric'],)).fetchone()
        current = db.execute('SELECT applications_json FROM frq_results WHERE id=?', (row['current_result_id'],)).fetchone()
        if not pinned or not old or not current:
            continue
        pinned_lines = _rubric_lines(json.loads(pinned[0]))
        old_lines = _rubric_lines(json.loads(old[0]))
        reworded = {line['id'] for line in old_lines} & {line['id'] for line in pinned_lines}
        changed = [o for o in old_lines for p in pinned_lines if o['id'] == p['id'] and o['description'] != p['description']]
        if not changed:
            continue
        for kind, applications in (('old', row['applications_json']), ('current', current[0])):
            lines = [{'line': a['rubric_item_id'], 'reason': (a.get('reason') or '')[:600]} for a in json.loads(applications) if a.get('applied')]
            if not lines:
                continue
            wanted = _quotes_removed(lines, changed, pinned_lines) if kind == 'old' else 0
            cases.append(Case('rubric_feedback', f"real-{row['result_id'][:10]}-{kind}", 'real',
                              _feedback_run(f"sandbox:{row['result_id']}:{kind}", {'lines': lines}, pinned_lines),
                              {'refers_to_unpinned_rubric': label(wanted, 'silver')} if wanted is not None else {},
                              {'response': row['response_id'], 'result': row['result_id'], 'which': kind, 'reworded_lines': len(reworded)}))
        seen_rubrics[row['pinned_rubric']] = seen_rubrics.get(row['pinned_rubric'], 0) + 1
        taken += 1
    return cases


def _quotes_removed(lines, changed, pinned_lines):
    """Silver label, in code: 1 when a reason repeats a number or three-word run that the old wording of a changed line had and the
    pinned rubric does not; None when it quotes nothing distinctive either way."""
    pinned_text = ' '.join(line['description'] for line in pinned_lines).casefold()
    removed = set()
    for line in changed:
        words = re.findall(r"[a-z0-9.^/=+-]+", line['description'].casefold())
        removed |= {' '.join(words[i:i + 3]) for i in range(len(words) - 2)} | {w for w in words if re.fullmatch(r'-?\d+(\.\d+)?(/\d+)?', w)}
    removed = {phrase for phrase in removed if phrase not in pinned_text}
    said = ' '.join(line['reason'] for line in lines).casefold()
    return 1 if any(phrase in said for phrase in removed) else None


def _has(db, table, column):
    return any(r[1] == column for r in db.execute(f'PRAGMA table_info("{table}")'))
