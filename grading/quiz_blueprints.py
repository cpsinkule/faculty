"""TEACHING-WORKFLOWS: course-wide blueprint pinned inside its immutable quiz draft.

Each slot names one objective, kind and points. Fixed questions retain the bank's
content revision; no bank or assessment is edited. Structural coverage is checked
mechanically; the instructor still checks whether a problem teaches its objective.
"""
import json
import math

from document_identity import quiz_latex
from question_bank.service import QuestionBankService


def pin(db, definition_id, value, total):
    if value is None:
        return None
    if not isinstance(value, list) or not 1 <= len(value) <= quiz_latex.MAX_PROBLEMS:
        raise ValueError('A blueprint needs 1 to 20 question slots.')
    nodes = {n['id']: n for n in QuestionBankService._outline(db, definition_id) if not n['retired'] and n['level'] == 'objective'}
    slots = []
    for position, entry in enumerate(value, 1):
        if not isinstance(entry, dict):
            raise ValueError('Each blueprint slot must be an object.')
        objective_id = entry.get('objective_id')
        objective = nodes.get(objective_id) if isinstance(objective_id, str) else None
        if not objective:
            raise ValueError(f'Question {position}: choose a current learning objective from this course’s bank.')
        if entry.get('objective_revision') is not None:
            if type(entry['objective_revision']) is not int or entry['objective_revision'] < 1:
                raise ValueError(f'Question {position}: choose a valid learning-objective revision.')
            selected = db.execute('SELECT title,revision FROM question_bank_node_revisions WHERE node_id=? AND revision=?',
                                  (objective['id'], entry['objective_revision'])).fetchone()
            if not selected:
                raise ValueError(f'Question {position}: the selected learning-objective revision is unavailable.')
            objective = {**objective, **dict(selected)}
        points = entry.get('points')
        if type(points) not in (int, float) or not math.isfinite(points) or not .5 <= points <= 100:
            raise ValueError(f'Question {position}: points must be between 0.5 and 100.')
        if not isinstance(entry.get('kind'), str) or entry['kind'] not in quiz_latex.KINDS:
            raise ValueError(f'Question {position}: choose multiple choice or free response.')
        slot = {'number': position, 'objective_id': objective['id'], 'objective_revision': objective['revision'],
                'objective': objective['title'], 'kind': entry['kind'], 'points': points}
        fixed = entry.get('question_id')
        if fixed is not None and not isinstance(fixed, str):
            raise ValueError(f'Question {position}: choose a valid bank question.')
        if fixed:
            revision = entry.get('question_revision')
            if type(revision) is not int or revision < 1:
                raise ValueError(f'Question {position}: choose a valid bank-question revision.')
            row = db.execute('''SELECT r.content_json,r.revision FROM question_bank_questions q
                JOIN question_bank_revisions r ON r.question_id=q.id WHERE q.id=? AND q.definition_id=? AND r.revision=?''',
                             (fixed, definition_id, revision)).fetchone()
            if not row:
                raise ValueError(f'Question {position}: the selected bank revision does not belong to this course.')
            content = json.loads(row['content_json'])
            if content['kind'] != slot['kind'] or content['points'] != points:
                raise ValueError(f'Question {position}: a fixed question must keep its bank type and points.')
            if any(part.get('points') is None for part in content['parts']):
                raise ValueError(f'Question {position}: set every part’s points in the bank before fixing this question.')
            # Standalone image assets are not valid quiz LaTeX; refusing prevents an incomplete printed question.
            pictures = QuestionBankService._pictures(db, [fixed]).get(fixed, [])
            if any(not picture['removed'] for picture in pictures):
                raise ValueError(f'Question {position}: fixed questions with separate picture assets need a LaTeX figure first.')
            for name in ('statement', 'figure', 'solution'):
                quiz_latex.fragment(content[name], f'Fixed question {position} {name}', quiz_latex.MAX_FIGURE if name == 'figure' else quiz_latex.MAX_FRAGMENT)
            slot['fixed'] = {'question_id': fixed, 'revision': revision, 'content': content}
        slots.append(slot)
    if abs(sum(s['points'] for s in slots) - total) > 1e-9:
        raise ValueError('Blueprint points must add up to the quiz’s total points.')
    return {'rule': 'quiz-blueprint-v1', 'slots': slots}


def check(quiz, blueprint):
    if not blueprint:
        return []
    slots = blueprint['slots']
    if len(quiz['problems']) != len(slots):
        raise ValueError(f'The blueprint requires exactly {len(slots)} problems in its stated order.')
    coverage = []
    for problem, slot in zip(quiz['problems'], slots):
        label = f"Blueprint question {slot['number']}"
        if problem['kind'] != slot['kind'] or abs(problem['points'] - slot['points']) > 1e-9:
            raise ValueError(f'{label} must keep its specified type and points.')
        if slot.get('fixed'):
            expected = slot['fixed']['content']
            if problem['parts'] != [{'label': p['label'], 'points': p['points']} for p in expected['parts']]:
                raise ValueError(f'{label} must keep its fixed parts and points.')
            for letter, content in problem['content'].items():
                for field in ('statement', 'figure', 'choices', 'correct_choice', 'final_answers'):
                    actual, wanted = content[field], expected[field]
                    if field == 'correct_choice':
                        actual, wanted = actual.lower(), wanted.lower()
                    if actual != wanted:
                        raise ValueError(f'{label}, version {letter}: keep the fixed bank {field.replace("_", " ")} exactly.')
                if content['parts'] != [{'label': p['label'], 'text': p['text']} for p in expected['parts']] or content['left'] or content['scaffold']:
                    raise ValueError(f'{label}, version {letter}: keep the fixed question’s text without extra scaffolding.')
        coverage.append({'number': slot['number'], 'objective': slot['objective'], 'points': slot['points'],
                         'kind': slot['kind'], 'versions': list(quiz['versions']), 'fixed': bool(slot.get('fixed'))})
    return coverage


INSTRUCTION = ('Follow request.blueprint.slots in order: each slot fixes the learning objective, question kind and points. '
               'These constraints override conflicting counts, kinds or points in request.format or examples; honor compatible format directions. '
               'All versions must cover the slot’s objective. A fixed.content is copied exactly into EVERY version, including '
               'statement, parts, choices, correct_choice, figure and final_answers; keep left and scaffold empty. '
               'Do not put blueprint identifiers or objective titles on the printed paper. These constraints apply to revisions too. '
               'Structural checks do not certify pedagogical alignment; the instructor reviews that.')
