"""TEACHING-WORKFLOWS: blueprint enforcement, pinning, and real drafting with a fake model."""
import copy
import unittest
import uuid

import test_quiz_drafts as fixture
from document_identity import quiz_latex
from grading import quiz_blueprints
from rosters import connect
from fake_openai import payload_of, prompt_name


class Blueprints(fixture.QuizDrafts):
    def test_blueprint_is_pinned_and_enforced_during_drafting(self):
        bank = self.app.extensions['question_bank']
        parent = None
        for level in ('chapter', 'section', 'topic', 'objective'):
            parent = bank.add_node(self.term['definition_id'], {'request_id': str(uuid.uuid4()), 'level': level,
                'parent_id': parent, 'title': 'Solve a linear equation'})['node_id']
        slots = [{'objective_id': parent, 'kind': kind, 'points': points} for kind, points in [('multiple_choice', 2), ('free_response', 3)]]
        data = {'request_id': str(uuid.uuid4()), 'term_id': self.term['id'], 'title': 'Blueprint quiz', 'total': 5,
                'versions': 2, 'mode': 'positive', 'instructions': 'Linear equations', 'blueprint': slots,
                'format': 'Leave generous space for handwritten work.'}
        draft = self.frq.create_quiz_draft(data, [])
        self.assertEqual(draft['blueprint']['slots'][0]['objective_id'], parent)
        self.assertEqual(self.frq.create_quiz_draft(data, [])['id'], draft['id'])
        attempts = []
        def answer(body, host):
            if prompt_name(body) == 'quiz_write':
                attempts.append(payload_of(body))
                if len(attempts) == 1:
                    wrong = fixture.quiz_answer(payload_of(body))
                    wrong['problems'][0]['points'] = 3
                    wrong['problems'][1]['points'] = 2
                    wrong['problems'][1]['parts'][1]['points'] = 1
                    return wrong  # total still 5: only the blueprint rejects this
            return self.answer(body, host)
        self.host.script = answer
        self.frq.process_cloud()
        self.assertGreaterEqual(len(attempts), 2)
        self.assertEqual(attempts[0]['request']['format'], data['format'])
        result = self.frq.quiz_draft(draft['id'])
        self.assertEqual(result['state'], 'ready', result['revisions'])
        quiz = result['revisions'][-1]['result']['quiz']
        self.assertEqual(len(result['revisions'][-1]['result']['blueprint_coverage']), 2)
        broken = copy.deepcopy(quiz)
        broken['problems'][0]['points'] = 3
        with self.assertRaisesRegex(ValueError, 'specified type and points'):
            quiz_blueprints.check(broken, draft['blueprint'])
        with connect(self.path) as db, self.assertRaisesRegex(ValueError, 'total points'):
            quiz_blueprints.pin(db, self.term['definition_id'], slots, 6)
        for malformed in ({'objective_id': []}, {'objective_revision': {}}, {'kind': []},
                          {'question_id': []}, {'question_id': 'selected', 'question_revision': []}):
            with self.subTest(malformed=malformed), connect(self.path) as db, self.assertRaises(ValueError):
                quiz_blueprints.pin(db, self.term['definition_id'], [{**slots[0], **malformed}], 2)
        # A fixed question is immutable content, not a prompt suggestion.
        written = fixture.quiz_answer({'request': {'versions': ['A']}})['problems'][0]
        content = {**written['versions'][0], 'kind': written['kind'], 'points': written['points']}
        added = bank.add_question(self.term['definition_id'], {'request_id': str(uuid.uuid4()), 'content': content})
        question = added['question'] if 'question' in added else added
        qid = question['id']
        with connect(self.path) as db:
            pinned = quiz_blueprints.pin(db, self.term['definition_id'], [{**slots[0], 'question_id': qid, 'question_revision': 1}], 2)
        clean = quiz_latex.clean({**fixture.quiz_answer({'request': {'versions': ['A']}}), 'problems': [written]}, ['A'], title_line='Quiz', total=2)
        quiz_blueprints.check(clean, pinned)
        clean['problems'][0]['content']['A']['statement'] += ' Changed.'
        with self.assertRaisesRegex(ValueError, 'fixed bank statement'):
            quiz_blueprints.check(clean, pinned)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(Blueprints(name) for name in Blueprints.__dict__ if name.startswith('test_'))
