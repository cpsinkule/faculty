"""Offline ASSISTANT scenario rubric; workspace development data only.

Feed a prepared run and the instructor's expected endpoint multiset to score a
scripted or recorded model plan. No model connection or external service is used.
Scores describe the whole plan; they are not a claim about model accuracy.
"""
from collections import Counter

from assistant.followup_plan import stage, problem


def _contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _contains(actual[k], v) for k, v in expected.items())
    return actual == expected


def evaluate(run, expected, *, needs_question=False, final_state=None, enrollment_id=None, allowed_recipients=None):
    actions = [a for a in run.get('actions', []) if not a.get('withdrawn')]
    specs = [{'endpoint': e} if isinstance(e, str) else e for e in expected]
    actual, expected = Counter(a['endpoint'] for a in actions), Counter(e['endpoint'] for e in specs)
    stages = [stage(a) for a in actions]
    remaining, incorrect = list(actions), 0
    for spec in specs:
        candidates = [a for a in remaining if a['endpoint'] == spec['endpoint']]
        exact = next((a for a in candidates if _contains(a.get('body', {}), spec.get('body', {}))), None)
        if exact is not None:
            remaining.remove(exact)
        elif candidates:
            incorrect += 1
            remaining.remove(candidates[0])
    missing = sum((expected - actual).values())
    return {
        'missing_steps': missing,
        'unexpected_steps': sum((actual - expected).values()),
        'unnecessary_questions': int(bool(run.get('question')) and not needs_question),
        'missing_questions': int(needs_question and not run.get('question')),
        'human_edits': sum(bool(a.get('edited')) for a in actions),
        'out_of_order': int(stages != sorted(stages)),
        'incorrect_completion': int(final_state == 'resolved' and (missing or incorrect or any(a.get('pending') or problem(a) for a in actions))),
        'incorrect_arguments': incorrect,
        'incorrect_student': sum(a.get('body', {}).get('enrollment_id', enrollment_id) != enrollment_id for a in actions) if enrollment_id else 0,
        'incorrect_recipients': sum(any(str(address).casefold() not in {r.casefold() for r in allowed_recipients}
                                    for field in ('to', 'cc', 'bcc') for address in a.get('body', {}).get(field, []))
                                    for a in actions) if allowed_recipients is not None else 0,
        'unsupported_claims': sum(c.get('supported') is not True for c in run.get('claims', [])),
    }
