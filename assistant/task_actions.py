"""ASSISTANT: typed workspace adapters; owners validate every read and write."""
from .tools import Tool, S, _schema


def required(value):
    if not isinstance(value, dict):
        raise ValueError('ASSISTANT: the owning record could not be read.')
    if value.get('error'):
        raise ValueError(value['error'])
    return value


def grading(c, a):
    body = {k: a[k] for k in ('event_id', 'assignment_id', 'term_id', 'section_ids') if k in a}
    if not body.get('event_id') and not (body.get('assignment_id') and body.get('term_id')):
        raise ValueError('ASSISTANT: name the exam event, or the assignment and term.')
    return c.write('POST', '/api/grading/assignments', body, 'Prepare grading and its existing blank templates')


def sitting(c, a):
    event = required(c.get('/api/exams/' + a['event_id']))
    body = {k: a[k] for k in ('label', 'kind', 'date', 'time', 'location', 'room_id') if k in a}
    body['version'] = event['version']
    path = '/api/exams/' + a['event_id'] + '/sittings'
    if a.get('sitting_id'):
        row = next((s for s in event['sittings'] if s['id'] == a['sitting_id']), None)
        if row is None:
            raise ValueError('EXAMS: choose a sitting belonging to this exam.')
        body['version'] = row['version']
        path += '/' + row['id']
    if a.get('participants'):
        if a.get('sitting_id'):
            raise ValueError('EXAMS: participants can be assigned only while creating a sitting.')
        body['participants'] = []
        for wanted in a['participants']:
            row = next((p for p in event['participants'] if all(p[k] == wanted[k] for k in ('section_id', 'enrollment_id'))), None)
            if row is None:
                raise ValueError('EXAMS: every participant must belong to this exam.')
            body['participants'].append({k: row[k] for k in ('section_id', 'enrollment_id', 'version')})
    return c.write('PATCH' if a.get('sitting_id') else 'POST', path, body, 'Arrange the assessment sitting')


def rubric(c, a):
    plan = required(c.get('/api/grading/assignments/' + a['plan_id']))
    question = next((q for q in plan.get('questions', []) if q['question_id'] == a['question_id']), None)
    if question is None:
        raise ValueError('GRADING: choose a question belonging to this grading plan.')
    body = {k: a[k] for k in ('lines', 'comments') if k in a}
    current = question.get('rubric')
    if not current:
        raise ValueError('GRADING: import a rubric before editing its lines.')
    body.update(version=question['version'], rubric_id=current['id'], sha256=current['sha256'])
    return c.write('POST', '/api/grading/assignments/' + a['plan_id'] + '/questions/' + a['question_id'] + '/rubric-edits',
                   body, 'Revise the selected rubric; grading and finalization retain their checks')


def pull(c, a):
    required(c.get('/api/gradebook/terms/' + a['term_id']))
    body = {'request_id': '$new_request_id', 'replace': a.get('replace', []),
            'absent_as_missing': a.get('absent_as_missing', False)}
    if a.get('section_id'):
        body['section_id'] = a['section_id']
    return c.write('POST', '/api/gradebook/terms/' + a['term_id'] + '/items/' + a['item_id'] + '/pull',
                   body, 'Review and pull final grading into the selected gradebook column')


def retry(c, a):
    kind, ident = a['kind'], a['id']
    path = {'inbox': '/api/inbox/jobs/', 'grading_job': '/api/grading/jobs/',
            'grading_preparation': '/api/grading/preparations/'}[kind] + ident
    view = required(c.get(path))
    if view.get('status') != 'failed':
        raise ValueError('PIPELINE: retry only a confirmed failed job; inspect interrupted or uncertain work explicitly.')
    body = {'version': view['version']} if kind == 'inbox' else {}
    return c.write('POST', path + '/retry', body, 'Retry this confirmed failure through its owning service')


def task_actions():
    ids = {'type': 'array', 'items': S, 'maxItems': 50}
    return [
        Tool('automation_status', 'Read configured automation scopes, schedules, last results and blockers. Configuration still uses the instructor’s controls.',
             _schema(), lambda c, a: c.get('/api/assistant/automations'), read=True),
        Tool('diagnose_blocked_work', 'Explain current workflow blockers from the owning services, optionally for one grading plan or Inbox job. Makes no retry and never clears instructor prerequisites.',
             _schema({'plan_id': S, 'job_id': S}), lambda c, a: c.get('/api/assistant/blockers', a), read=True),
        Tool('retry_failed_work', 'Only when the instructor asks: retry one confirmed failed Inbox job, grading job or grading preparation through its normal route. Never repeats an uncertain/interrupted outcome; certified-work rules still apply.',
             _schema({'kind': {'type': 'string', 'enum': ['inbox', 'grading_job', 'grading_preparation']}, 'id': S}, ('kind', 'id')), retry),
        Tool('import_teaching_material', 'Propose importing generated text or a retained course-wide artifact into an assignment version, quiz draft, question rubric, or course notes. Requires approval. LaTeX must be a complete document for assignment versions/notes; a quiz needs term_id, versions and total. Never prints or publishes.',
             _schema({'kind': {'type': 'string', 'enum': ['assignment_version', 'quiz_draft', 'rubric', 'course_notes']},
                      'title': S, 'content': S, 'artifact_id': S, 'definition_id': S, 'term_id': S, 'assignment_id': S,
                      'plan_id': S, 'question_id': S, 'mode': S, 'versions': {'type': 'integer'}, 'total': {'type': 'number'},
                      'format': S, 'instructions': S, 'blueprint': {'type': 'array', 'items': {'type': 'object'}}}, ('kind', 'title')),
             lambda c, a: c.write('POST', '/api/assistant/materials', {**a, 'request_id': '$new_request_id'}, 'Import teaching material: ' + a['title'])),
        Tool('prepare_grading', 'Prepare the grading plan and blank templates for an existing assessment. Uses the owner route and requires approval.',
             _schema({'event_id': S, 'assignment_id': S, 'term_id': S, 'section_ids': ids}), grading),
        Tool('arrange_assessment', 'Prepare a new sitting or update one existing sitting, using current owner revisions. New sittings may seat exact existing participants. Requires approval.',
             _schema({'event_id': S, 'sitting_id': S, 'label': S, 'kind': {'type': 'string', 'enum': ['main', 'makeup', 'dr']},
                      'date': S, 'time': S, 'location': S, 'room_id': S, 'participants': {'type': 'array', 'maxItems': 50,
                      'items': _schema({'section_id': S, 'enrollment_id': S}, ('section_id', 'enrollment_id'))}}, ('event_id', 'label')), sitting),
        Tool('revise_question_rubric', 'Prepare rubric wording/point edits or line comments for one question. Reads the current plan revision; wording/points require approval and renewed finalization.',
             _schema({'plan_id': S, 'question_id': S, 'lines': {'type': 'array', 'maxItems': 1000,
                      'items': _schema({'item_id': S, 'description': S, 'points': {'type': ['number', 'string']}}, ('item_id',))},
                      'comments': {'type': 'object', 'additionalProperties': S}}, ('plan_id', 'question_id')), rubric),
        Tool('pull_final_grades', 'Prepare a gradebook pull with its owner preview. By default keep typed values; never certify or send grades.',
             _schema({'term_id': S, 'item_id': S, 'section_id': S, 'replace': ids, 'absent_as_missing': {'type': 'boolean'}}, ('term_id', 'item_id')), pull),
        Tool('review_grade_delivery', 'Read one assessment’s certification, gradebook and retained delivery evidence. Receipt dates are not live remote checks.',
             _schema({'plan_id': S}, ('plan_id',)), lambda c, a: c.get('/api/teaching-review/' + a['plan_id'] + '/delivery'), read=True),
    ]
