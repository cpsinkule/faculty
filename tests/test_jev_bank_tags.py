"""JEV BANK-TAGS (docs/decisions/question-bank.md, docs/decisions/jev.md; requirement, 2026-10-03): Jev places a question's section,
topic, difficulty and types first; the drafting model writes only the objective of a question Jev is sure of and tags the rest
entirely. A scripted Jev and the fake model host only; nothing reaches TypeSafe."""
import json
import unittest
from unittest.mock import patch

import test_question_bank as bank_fixture
from fake_openai import payload_of, prompt_name
from jev import client
from question_bank import jev_tags
from rosters import connect


def jev_answer(state, questions, *, section_pick, topic_pick=None, reversed_section=None, confidence=.95, difficulty=1, types=('computation',)):
    """A fake `jev.client.evaluate`: the first request (section/difficulty/types) or the topic request, by its questions."""
    answers = {}
    for name, question in questions.items():
        if question['type'] == 'choice':
            base = name.removesuffix('__reversed')
            pick = (reversed_section if reversed_section and name == 'section__reversed' else section_pick) if base == 'section' else topic_pick
            pick = next(option for option in question['criteria'] if option.endswith(pick))
            answers[name] = {'type': 'choice', 'choice': pick, 'confidence': confidence,
                             'probabilities': {o: 1.0 if o == pick else 0.0 for o in question['criteria']}}
        elif question['type'] == 'score':
            answers[name] = {'type': 'score', 'score': float(difficulty), 'confidence': .9,
                             'probabilities': {str(i): 1.0 if i == difficulty else 0.0 for i in range(3)}}
        else:
            answers[name] = {'type': 'noul', 'noul': .9 if name.removeprefix('type_') in types else .05}
    return {'model': client.MODEL, 'answers': answers, 'usage': {'input_tokens': 100, 'output_tokens': 5}}


class BankTagTests(unittest.TestCase):
    # The bank fixture and helpers, without re-running test_question_bank's own tests.
    setUp = bank_fixture.Bank.setUp
    post, view, write, asked = bank_fixture.Bank.post, bank_fixture.Bank.view, bank_fixture.Bank.write, bank_fixture.Bank.asked

    def answer(self, body, host):
        """The model: a `placed` question gets its objective under its placed entry (unless `stray` puts it elsewhere once)."""
        if prompt_name(body) != 'bank_tag':
            return ('status', 500, {}, json.dumps({'error': 'not scripted'}))
        payload = payload_of(body)
        entries = {e['ref']: e for e in payload['outline']}
        other = next(e['ref'] for e in payload['outline'] if e['level'] == 'section' and e['number'] == '1.2')
        answers = []
        for q in payload['questions']:
            home = q['placed']['nodes'][0] if 'placed' in q else next(e['ref'] for e in payload['outline'] if e['level'] == 'section')
            parent = other if self.stray and 'placed' in q and not self.strayed else home
            self.strayed = self.strayed or parent != home
            answers.append({'ref': q['ref'], 'nodes': [home], 'difficulty': 'hard', 'types': ['concept'], 'reason': 'Model reason.',
                            'objectives': [{'ref': '', 'parent': parent, 'text': f"Work with {entries[parent]['title'].lower()}"}]})
        return {'questions': answers, 'notes': ''}

    def outline(self):
        node = lambda body: self.post(f'/api/question-bank/definitions/{self.definition}/nodes', body)['node_id']  # noqa: E731
        chapter = node({'request_id': 'c', 'level': 'chapter', 'number': '1', 'title': 'Matrices'})
        self.s11 = node({'request_id': 's1', 'level': 'section', 'number': '1.1', 'parent_id': chapter, 'title': 'Matrix Operations'})
        self.s12 = node({'request_id': 's2', 'level': 'section', 'number': '1.2', 'parent_id': chapter, 'title': 'Inverses'})
        self.add = node({'request_id': 't1', 'level': 'topic', 'parent_id': self.s11, 'title': 'Adding Matrices'})
        self.mul = node({'request_id': 't2', 'level': 'topic', 'parent_id': self.s11, 'title': 'Multiplying Matrices'})
        self.inv = node({'request_id': 't3', 'level': 'topic', 'parent_id': self.s12, 'title': 'Finding Inverses'})

    def run_tagging(self, evaluate):
        self.bank.sync(self.definition)
        with patch('jev.client.available', return_value=True), patch('jev.client.evaluate', side_effect=evaluate) as called:
            self.frq.process_cloud()
        return called

    def classification(self, question):
        return {q['id']: q for q in self.view()['questions']}[question['id']]['classification']

    def script(self, state, questions):
        text = state['question']['text']
        if 'inverse' in text:
            return jev_answer(state, questions, section_pick='Inverses', difficulty=2, types=('computation', 'concept'))
        if 'store' in text:
            return jev_answer(state, questions, section_pick='Matrix Operations', reversed_section='Inverses')  # orders disagree
        return jev_answer(state, questions, section_pick='Matrix Operations', topic_pick='Multiplying Matrices', difficulty=0)

    def setUp_case(self, stray=False):
        self.stray, self.strayed = stray, False
        self.outline()

    def test_jev_places_what_it_is_sure_of_and_the_model_writes_only_those_objectives(self):
        self.setUp_case()
        sure = self.write('Find $AB$ when $A$ and $B$ are $2 \\times 2$.')
        single = self.write('Find the inverse of $A$.')
        doubt = self.write('A store sells two products; write the matrices.')
        called = self.run_tagging(self.script)
        # Sure: first request, then the topic among Matrix Operations' two topics; the single-topic section is never asked its topic.
        asked = [set(c.args[1]) for c in called.call_args_list]
        self.assertEqual(sum('section' in q for q in asked), 3)
        self.assertEqual([sorted(q['topic']['criteria']) for q in (c.args[1] for c in called.call_args_list) if 'topic' in q],
                         [['1.1 · Adding Matrices', '1.1 · Multiplying Matrices']])
        [request] = self.asked('bank_tag')
        placed = {q['text'][:9]: q.get('placed') for q in request['questions']}
        refs = {e['ref']: e['title'] for e in request['outline']}
        self.assertEqual({k: (refs[v['nodes'][0]], v['difficulty'], v['types']) if v else None for k, v in placed.items()},
                         {'Find $AB$': ('Multiplying Matrices', 'easy', ['computation']),
                          'Find the ': ('Finding Inverses', 'hard', ['computation', 'concept']), 'A store s': None})
        # Jev's placement stands; the model's objective is kept; the model tags the doubt entirely.
        view = {n['id']: n for n in self.view()['outline']}
        first = self.classification(sure)
        self.assertEqual((first['nodes'][0], first['difficulty'], first['types']), (self.mul, 'easy', ['computation']))
        self.assertEqual(view[first['nodes'][1]]['level'], 'objective')
        self.assertIn('placed by Jev', first['reason'])
        self.assertEqual(self.classification(single)['nodes'][0], self.inv)
        model = self.classification(doubt)
        self.assertEqual((model['nodes'][0], model['difficulty'], model['types'], model['reason']), (self.s11, 'hard', ['concept'], 'Model reason.'))
        with connect(self.path) as db:
            stored = json.dumps([dict(r) for r in db.execute("SELECT * FROM jev_calls WHERE purpose='bank-tag'")])
            result = json.loads(db.execute("SELECT result_json FROM question_bank_runs WHERE kind='tag'").fetchone()[0])
        self.assertNotIn('store sells', stored)  # receipts keep hashes, never the question text
        self.assertEqual(sum('jev' in q for q in result['questions']), 2)

    def test_an_objective_outside_the_placement_is_asked_again(self):
        self.setUp_case(stray=True)
        sure = self.write('Find $AB$ when $A$ and $B$ are $2 \\times 2$.')
        self.run_tagging(self.script)
        self.assertEqual(len(self.asked('bank_tag')), 2)
        self.assertIn('must sit under 1.1', self.asked('bank_tag')[1]['refused'])
        objective = {n['id']: n for n in self.view()['outline']}[self.classification(sure)['nodes'][1]]
        self.assertEqual(objective['parent_id'], self.mul)

    def test_jev_off_or_failing_leaves_tagging_as_before(self):
        self.setUp_case()
        question = self.write('Find $AB$ when $A$ and $B$ are $2 \\times 2$.')
        self.bank.sync(self.definition)
        self.frq.process_cloud()  # Jev is not configured here
        [request] = self.asked('bank_tag')
        self.assertNotIn('placed', request['questions'][0])
        self.assertEqual(self.classification(question)['reason'], 'Model reason.')
        again = self.write('Find $BA$.')
        called = self.run_tagging(lambda state, questions: (_ for _ in ()).throw(client.DecisionError('HTTP 500')))
        self.assertEqual(called.call_count, 1)
        self.assertNotIn('placed', self.asked('bank_tag')[1]['questions'][0])
        self.assertEqual(self.classification(again)['reason'], 'Model reason.')


class PlacementTests(unittest.TestCase):
    def test_low_confidence_or_no_type_is_a_doubt(self):
        outline = [{'id': 's', 'level': 'section', 'number': '1.1', 'title': 'Ops', 'parent_id': 'c'},
                   {'id': 'c', 'level': 'chapter', 'number': '1', 'title': 'M', 'parent_id': None}]
        asked, sections, topics = jev_tags.questions_for(outline)
        every = {n['id']: n for n in outline}
        good = {'status': 'complete', 'id': 'x', **jev_answer({}, asked, section_pick='Ops')}
        self.assertEqual(jev_tags.placement(good, None, sections, topics, every)['nodes'], ['s'])
        for change in ({'confidence': .7}, {'types': ()}):
            weak = {'status': 'complete', 'id': 'x', **jev_answer({}, asked, section_pick='Ops', **change)}
            self.assertIsNone(jev_tags.placement(weak, None, sections, topics, every), change)
        self.assertIsNone(jev_tags.placement({'status': 'failed'}, None, sections, topics, every))


    def test_a_printed_section_is_never_asked_and_a_printed_chapter_bounds_the_section(self):
        outline = [{'id': 'c', 'level': 'chapter', 'number': '1', 'title': 'M', 'parent_id': None},
                   {'id': 'c2', 'level': 'chapter', 'number': '2', 'title': 'L', 'parent_id': None},
                   {'id': 's', 'level': 'section', 'number': '1.1', 'title': 'Ops', 'parent_id': 'c'},
                   {'id': 's2', 'level': 'section', 'number': '1.2', 'title': 'Inverses', 'parent_id': 'c'},
                   {'id': 't2', 'level': 'topic', 'number': '', 'title': 'Finding Inverses', 'parent_id': 's2'}]

        class Jev:
            asked = []

            def ask(self, purpose, owner, state, questions, rule):
                self.asked.append(sorted(questions))
                return {'status': 'complete', 'id': 'x', **jev_answer(state, questions, section_pick='Ops')}
        question = {'id': 'q', 'revision': 1, 'content': {'kind': 'free_response', 'points': None, 'statement': 'Find $A^{-1}$.', 'parts': [],
                                                          'choices': [], 'final_answers': [], 'solution': '', 'notes': ''}}
        jev = Jev()
        with patch('jev.client.available', return_value=True):
            placed = jev_tags.place(jev, 'd', [question], outline, printed={'q': 's2'})
            self.assertEqual((placed['q']['nodes'], placed['q']['confidence']['section']), (['t2'], 'printed'))  # its only topic, decided in code
            self.assertFalse(any(name.startswith('section') for name in jev.asked[0]))
            self.assertEqual(jev_tags.place(Jev(), 'd', [question], outline, printed={'q': 'c2'}), {})  # Jev's 1.1 is not in chapter 2
            self.assertEqual(jev_tags.place(Jev(), 'd', [question], outline, printed={'q': 'c'})['q']['nodes'], ['s'])


if __name__ == '__main__':
    unittest.main()
