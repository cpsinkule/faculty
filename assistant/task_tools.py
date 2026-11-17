"""Typed preparation tools. All access goes through the app's ordinary routes."""
from .tools import Tool, S, I, _schema


def prepare(context, args, kind, target):
    return context.write('POST', '/api/assistant/preparations', {'kind': kind, 'target': target, 'request_id': '$new_request_id'},
                         'Prepare ' + kind + ' for instructor review')


def makeup(context, args):
    student = context.get('/api/student-support/students/' + args['enrollment_id'] + '/context')
    if student.get('error'):
        return student
    # The existing absence/booking service remains the sole authority on what is owed and offered.
    owed = context.get('/api/absences/makeups')
    if owed.get('error'):
        return owed
    return {'student': student, 'makeups': [m for m in owed.get('makeups', []) if m.get('enrollment_id') == args['enrollment_id']],
            'states': owed.get('states'), 'booking_policy': owed.get('booking', {}).get('terms', {}).get(student['term_id']),
            'next': 'Use the owning absence and exam routes for exact proposals. Never invent a sitting or override booking holds.'}


def task_tools():
    return [
        Tool('continue_preparation', 'During a background preparation only: schedule the next stage after this stage’s exact teaching actions succeed. Use when later work needs their real result IDs. Every new teaching change still requires approval.',
             _schema({'instruction': S, 'after_action_ids': {'type': 'array', 'items': S, 'minItems': 1, 'maxItems': 20}}, ('instruction', 'after_action_ids')),
             lambda c, a: c.write('POST', '/api/assistant/preparations/' + str(c.session.get('_workflow_run', '')) + '/continue',
                                  {**a, 'request_id': '$new_request_id'}, 'Continue the reviewed plan after its prerequisites succeed')),
        Tool('assessment_readiness', 'Inspect one assessment’s owning readiness projection: paper, grading prerequisites, seating, notices and printing. Read only.',
             _schema({'event_id': S}, ('event_id',)), lambda c, a: c.get('/api/assistant/readiness/' + a['event_id']), read=True),
        Tool('grade_reconciliation', 'Compare final grading with a term’s gradebook and last recorded Canvas state. No live Canvas claim and no writes.',
             _schema({'term_id': S}, ('term_id',)), lambda c, a: c.get('/api/assistant/reconciliation/' + a['term_id']), read=True),
        Tool('prepare_assessment', 'When the instructor asks, start one complete assessment-readiness preparation. Analysis starts immediately; resulting teaching changes still need approval.',
             _schema({'event_id': S}, ('event_id',)), lambda c, a: prepare(c, a, 'readiness', a['event_id'])),
        Tool('prepare_grade_reconciliation', 'When the instructor asks, start a complete review of grading, gradebook and retained source discrepancies for this term. Analysis starts immediately; no automatic corrections.',
             _schema({'term_id': S}, ('term_id',)), lambda c, a: prepare(c, a, 'reconciliation', a['term_id'])),
        Tool('makeup_arrangements', 'Read one student’s context and current owed makeups/booking state before proposing arrangements through the existing routes.',
             _schema({'enrollment_id': S}, ('enrollment_id',)), makeup, read=True),
        Tool('enable_preparation', 'Only when the instructor asks: propose enabling preparation on changes for an exact assessment (readiness), or daily at 5:00 AM Central for a term (reconciliation). Every resulting change remains an approval card.',
             _schema({'kind': {'type': 'string', 'enum': ['readiness', 'reconciliation']}, 'target': S, 'lead_days': I}, ('kind', 'target')),
             lambda c, a: c.write('POST', '/api/assistant/workflows', {**a, 'request_id': '$new_request_id'}, 'Enable automatic ' + a['kind'] + ' preparation')),
    ]
