"""Rubrics drafted from a blank template, for any assignment: a draft creates nothing until the instructor keeps it, edited or not.

Synthetic template pages and a loopback model host. What the instructor accepts becomes an ordinary draft question through the
rubric upload's own path, checked and approved later in Key & Rubric Finalization like an uploaded rubric."""
import json
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import Conflict
from rosters import connect
from fake_openai import FakeHost, images_of, payload_of, prompt_name, survey_answer, write_answer
from frq_fixtures import confirm_for_review
from test_grading_cloud import endpoint
from test_grading_locations import confirm_templates
import test_grading_uploads as upload_fixture
from grading.rubric_quality import builtin_exclusions
from grading.scoring import score
from grading.models import digest, encoded


class RubricDrafts(unittest.TestCase):
    managed = False
    document_version = upload_fixture.RubricUploads.document_version
    ready_batch = upload_fixture.RubricUploads.ready_batch
    reviewed_source = upload_fixture.RubricUploads.reviewed_source
    upload = upload_fixture.RubricUploads.upload
    uploaded_questions = upload_fixture.RubricUploads.uploaded_questions
    template = upload_fixture.RubricUploads.template

    def setUp(self):
        upload_fixture.RubricUploads.setUp(self)
        self.host = FakeHost().start()
        self.addCleanup(self.host.stop)
        self.configure('alpha-vision')
        self.blank = self.template()
        self.host.script = lambda body, host: survey_answer(('18', '19')) if prompt_name(body) == 'rubric_survey' else write_answer(payload_of(body))

    def configure(self, reviewer):
        endpoints = [endpoint(self.host, 'alpha-vision'), endpoint(self.host, 'beta-text', modality='text')]
        tiers = {tier: {'reader': 'alpha-vision', 'primary': 'alpha-vision', 'second': '', 'verifier': ''} for tier in ('cloud_standard', 'cloud_high_assurance')}
        current = self.frq.settings()
        self.frq.save_settings({'version': current['version'], 'settings': {**current['settings'], 'endpoints': endpoints, 'tiers': tiers,
            'yardstick': '', 'max_retries': 3, 'rubric_reviewer': reviewer}})

    def draft(self, **extra):
        slot = self.frq.assignment_templates(self.plan['id'])['slots'][0]
        command = {'key': slot['key'], 'template_id': self.blank['id'], 'request_id': str(uuid.uuid4()), **extra}
        queued = self.frq.queue_rubric_generation(self.plan['id'], command)
        self.assertEqual(queued['status'], 'pending')
        self.assertEqual(self.frq.queue_rubric_generation(self.plan['id'], command)['id'], queued['id'])  # The same request is the same draft.
        self.frq.process_cloud()
        return self.frq.rubric_generation(self.plan['id'], queued['id'])

    def calls(self, gid):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute('SELECT step,task_attempt,state FROM frq_cloud_calls WHERE rubric_generation_id=? ORDER BY rowid', (gid,))]

    def keep(self, draft, **changes):
        return {'draft': draft['number'], 'title': draft['title'], 'printed_number': draft['number'], 'max_points': draft['points'], 'items': draft['items'],
                'statement': draft['statement'], 'solution': draft['solution'], **changes}

    def test_a_multiple_choice_draft_is_two_lines_without_work_and_its_key_takes_the_label_or_the_content(self):
        from grading.answers import key_from_rubric, rubric_roles
        from grading.rubric_generation import written_rubric
        problem = {'number': '1', 'points': 2, 'parts': []}
        choice = 'Correct choice: $$(c)$$ $$y=-\\frac{3}{2}x+4$$'
        for mode, lines in (('positive', [(choice, 2), ('No answer or completely incorrect', 0)]), ('negative', [(choice, 0), ('Wrong choice, more than one choice, or no choice', -2)])):
            drafted = written_rubric({'points': 2, 'items': [{'description': d, 'points': p} for d, p in lines]}, problem, mode)
            self.assertEqual(drafted['warnings'], [], mode)  # nothing asks for step lines that add up to the total
        rules = {'mode': 'positive', 'ceiling': 2, 'items': [{'id': 'R1', 'description': choice, 'points': 2}, {'id': 'R2', 'description': 'No answer or completely incorrect', 'points': 0}]}
        self.assertFalse(rubric_roles(rules)['work_required'])  # so full credit is never held for "little work shown"
        self.assertEqual(key_from_rubric(rules), [{'label': '', 'accept': ['(c)', 'y=-\\frac{3}{2}x+4']}])

    def test_templates_come_first_and_the_draft_creates_nothing_until_kept(self):
        # A template can be chosen before any rubric exists; confirming it still waits for the rubrics.
        state = self.frq.assignment_templates(self.plan['id'])
        self.assertTrue(state['can_choose'])
        self.assertFalse(state['ready'])
        generation = self.draft()
        self.assertEqual(generation['status'], 'complete')
        self.assertEqual(generation['mode'], 'positive')
        self.assertEqual(self.uploaded_questions(), [])
        drafted = generation['result']['questions']
        self.assertEqual([q['number'] for q in drafted], ['18', '19'])
        self.assertEqual((drafted[0]['points'], len(drafted[0]['items']), drafted[0]['warnings']), (10, 4, []))
        self.assertEqual(generation['result']['failed'], [])
        # One survey of the whole exam with every page, then each problem alone with its own page.
        requests = [r['body'] for r in self.host.requests]
        self.assertEqual([prompt_name(b) for b in requests], ['rubric_survey', 'rubric_write', 'rubric_write'])
        self.assertEqual(len(images_of(requests[0])), 2)
        # Both contracts speak of any assignment; the writer also checks its own wording for ambiguity.
        self.assertTrue(requests[0]['messages'][0]['content'].startswith('You list the problems printed on ONE blank assignment'))
        self.assertTrue(requests[1]['messages'][0]['content'].startswith('You draft the grading rubric for ONE problem of an assignment'))
        self.assertIn('perform an ambiguity check on EVERY line', requests[1]['messages'][0]['content'])
        # Multiple choice is pass/fail on the choice alone (requirement, 2026-09-25): the writer is told so.
        self.assertIn('MULTIPLE-CHOICE problems are graded pass/fail on the choice alone', requests[1]['messages'][0]['content'])
        self.assertTrue(all(payload_of(b)['mode'] == 'positive' for b in requests[1:]))
        self.assertEqual(sorted(payload_of(requests[0])), ['assignment', 'page_text', 'pages'])
        self.assertEqual(drafted[0]['title'], 'Question 18')  # A printed "18." says no more than its number.
        self.assertEqual(payload_of(requests[0])['page_text'], [{'page': 1, 'text': 'Problem on page 1'}, {'page': 2, 'text': 'Problem on page 2'}])
        self.assertEqual(sorted(payload_of(b)['problem']['number'] for b in requests[1:]), ['18', '19'])
        self.assertTrue(all(len(images_of(b)) == 1 and 'rubric' not in payload_of(b) for b in requests[1:]))
        self.assertEqual({c['state'] for c in self.calls(generation['id'])}, {'completed'})
        # The blank exam the draft was read from is now the version's default template, still to be confirmed.
        self.assertEqual(self.frq.assignment_templates(self.plan['id'])['slots'][0]['template_id'], self.blank['id'])

        first = drafted[0]
        lines = [dict(line) for line in first['items']]
        lines[1]['description'] = 'Adds 8 to both sides to get $$3x = 18$$'
        command = {'request_id': str(uuid.uuid4()), 'questions': [self.keep(first, items=lines, solution='x = 6')]}
        result = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], command)
        self.assertEqual([r['status'] for r in result['results']], ['imported'])
        self.assertEqual(result['generation']['created'], ['18'])
        self.assertEqual(self.frq.accept_rubric_generation(self.plan['id'], generation['id'], command)['results'][0]['status'], 'already_imported')
        [q] = self.uploaded_questions()
        self.assertEqual((q['question_key'], q['content']['printed_number'], q['content']['max_points'], q['content']['title']), ('q18', '18', 10, 'Question 18'))
        self.assertEqual((q['content']['problem'], q['content']['worked_solution'], q['content']['answer_key']), (first['statement'], 'x = 6', []))
        rubric = q['rubric']
        self.assertIsNone(rubric['approved_at'])
        self.assertEqual(rubric['rules']['mode'], 'positive')
        self.assertEqual([(i['id'], i['points']) for i in rubric['rules']['items']], [('R1', 10), ('R2', 5), ('R3', 5), ('R4', 0)])
        self.assertEqual(rubric['rules']['items'][1]['description'], 'Adds 8 to both sides to get $$3x = 18$$')
        self.assertEqual((rubric['compiler']['name'], rubric['compiler']['generation_id'], rubric['compiler']['edited']), ('model-drafted-v1', generation['id'], True))
        self.assertIn('Key & Rubric Finalization', rubric['rules']['warnings'][0])
        # The finalization's own rules read it like any rubric, and the key is only a proposal read from the full-credit line.
        self.assertEqual(builtin_exclusions(rubric['rules'], 10), [{'item_id': 'R1', 'rule': 'full_credit'}, {'item_id': 'R4', 'rule': 'no_credit'}])
        self.assertEqual(q['answer']['proposed_key'], [{'label': '', 'accept': ['x = 6']}])
        _, original = self.inbox.read_artifact(rubric['source_artifact_id'])
        self.assertEqual(original.decode(), rubric['source_text'])
        self.assertIn('after editing', json.loads(rubric['source_text'])['notes'])
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_rubric_uploads WHERE assignment_id=?', (self.plan['id'],)).fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        # The problem left out can be kept later; a number that already exists is refused for that question alone.
        later = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], {'request_id': str(uuid.uuid4()),
            'questions': [self.keep(drafted[1]), self.keep(drafted[0], printed_number='18', solution='changed')]})
        self.assertEqual([r['status'] for r in later['results']], ['imported', 'failed'])
        self.assertIn('already exists', later['results'][1]['error'])
        self.assertEqual(later['generation']['created'], ['18', '19'])
        self.assertEqual(len(self.uploaded_questions()), 2)
        self.assertTrue(self.frq.assignment_templates(self.plan['id'])['ready'])

    def test_negative_drafts_keep_the_mode_through_generation_edit_acceptance_and_scoring(self):
        generation = self.draft(mode='negative')
        self.assertEqual((generation['status'], generation['mode']), ('complete', 'negative'))
        draft = generation['result']['questions'][0]
        self.assertEqual((draft['mode'], draft['points'], draft['warnings']), ('negative', 10, []))
        self.assertEqual([i['points'] for i in draft['items']], [0, -5, -5])  # No no-credit line in negative scoring (2026-09-28).
        self.assertEqual(self.uploaded_questions(), [])
        writes = [r['body'] for r in self.host.requests if prompt_name(r['body']) == 'rubric_write']
        for body in writes:
            self.assertEqual(payload_of(body)['mode'], 'negative')
            points = body['response_format']['json_schema']['schema']['properties']['items']['items']['properties']['points']
            self.assertEqual((points['minimum'], points['maximum']), (-10, 0))
        refused = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], {'request_id': str(uuid.uuid4()),
            'questions': [self.keep(draft, mode='positive')]})
        self.assertEqual(refused['results'][0]['status'], 'failed')
        self.assertIn('mode must match', refused['results'][0]['error'])
        self.assertEqual(self.uploaded_questions(), [])
        lines = [dict(i) for i in draft['items']]
        lines[1]['points'] = -3.5
        command = {'request_id': str(uuid.uuid4()), 'questions': [self.keep(draft, mode='negative', items=lines)]}
        result = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], command)
        self.assertEqual(result['results'][0]['status'], 'imported')
        self.assertEqual(self.frq.accept_rubric_generation(self.plan['id'], generation['id'], command)['results'][0]['status'], 'already_imported')
        [question] = self.uploaded_questions()
        rubric = question['rubric']
        self.assertEqual(rubric['rules']['mode'], 'negative')
        self.assertEqual(rubric['compiler']['mode'], 'negative')
        self.assertIsNone(rubric['approved_at'])
        self.assertEqual(builtin_exclusions(rubric['rules'], 10), [])
        _, original = self.inbox.read_artifact(rubric['source_artifact_id'])
        self.assertEqual(json.loads(original)['mode'], 'negative')
        self.assertEqual(score(rubric['rules'], [], 10)['score'], 10)
        self.assertEqual(score(rubric['rules'], [{'rubric_item_id': 'R2', 'applied': True}], 10)['score'], 6.5)
        # A blank or wholly wrong response takes every deduction; alternatives past the total floor at 0.
        self.assertEqual(score(rubric['rules'], [{'rubric_item_id': 'R2', 'applied': True}, {'rubric_item_id': 'R3', 'applied': True}], 10)['score'], 1.5)

    def test_a_deducting_drafts_parts_are_capped_at_the_worths_its_writer_states(self):
        # GRADING "PART CAPS" (requirement, 2026-10-15): nothing prints the parts' points, so the writer divides the total, and
        # accepting the draft makes each part one cap at its worth: Part B's four misses no longer take Part A's half point.
        survey = survey_answer(('3',), points=2)
        survey['problems'][0]['parts'] = [{'label': '(a)', 'points': None}, {'label': '(b)', 'points': None}]
        written = {'solution': '', 'final_answers': [], 'points': 2, 'parts': [{'part': 'a', 'points': 0.5}, {'part': 'b', 'points': 1.5}], 'notes': '',
                   'items': [{'description': 'Correct answer with supporting work: bounded; $$(0,0),\\ (0,10),\\ (12,4)$$', 'points': 0, 'clarification': ''},
                             {'description': 'Part A: The answer is not "bounded"', 'points': -0.5, 'clarification': ''},
                             *({'description': f'Part B: Corner point {n} is missing or incorrect', 'points': -0.5, 'clarification': ''} for n in (1, 2, 3, 4))]}
        self.host.script = lambda body, host: survey if prompt_name(body) == 'rubric_survey' else written
        generation = self.draft(mode='negative')
        [draft] = generation['result']['questions']
        self.assertEqual((draft['part_points'], draft['warnings']), ({'a': 0.5, 'b': 1.5}, []))
        prompt = next(r['body'] for r in self.host.requests if prompt_name(r['body']) == 'rubric_write')['messages'][0]['content']
        self.assertIn("Faculty stops each part's deductions at that part's worth", prompt)
        result = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], {'request_id': str(uuid.uuid4()), 'questions': [self.keep(draft, mode='negative')]})
        self.assertEqual(result['results'][0]['status'], 'imported')
        [question] = self.uploaded_questions()
        rules = question['rubric']['rules']
        self.assertEqual([(g['id'], g['floor']) for g in rules['groups']], [('part-a-cap', -0.5), ('part-b-cap', -1.5)])
        self.assertEqual([i['group'] for i in rules['items']], [None, 'part-a-cap'] + ['part-b-cap'] * 4)
        graded = score(rules, [{'rubric_item_id': f'R{n}', 'applied': True} for n in (3, 4, 5, 6)], 2)
        self.assertEqual((graded['score'], graded['part_caps']), (0.5, 0.5))

    def test_mode_is_validated_and_bound_to_the_generation_request(self):
        slot = self.frq.assignment_templates(self.plan['id'])['slots'][0]
        command = {'key': slot['key'], 'template_id': self.blank['id'], 'request_id': str(uuid.uuid4())}
        for mode in ('deductions', '', None, False, [], {}):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'Scoring must be'):
                self.frq.queue_rubric_generation(self.plan['id'], {**command, 'mode': mode})
        queued = self.frq.queue_rubric_generation(self.plan['id'], command)
        self.assertEqual(queued['mode'], 'positive')
        self.assertEqual(self.frq.queue_rubric_generation(self.plan['id'], {**command, 'mode': 'positive'})['id'], queued['id'])
        with self.assertRaisesRegex(Conflict, 'already belongs'):
            self.frq.queue_rubric_generation(self.plan['id'], {**command, 'mode': 'negative'})
        with connect(self.path) as db:
            saved = json.loads(db.execute('SELECT input_json FROM frq_rubric_generations WHERE id=?', (queued['id'],)).fetchone()[0])
        self.assertEqual((saved['mode'], saved['rule']), ('positive', 'rubric-draft-v5'))

    def test_wrong_signs_are_rejected_in_both_model_output_and_instructor_edits(self):
        for mode, wrong in (('negative', 'positive'), ('positive', 'negative')):
            with self.subTest(mode=mode):
                self.host.script = lambda body, host: survey_answer(('18',)) if prompt_name(body) == 'rubric_survey' else write_answer({**payload_of(body), 'mode': wrong})
                generation = self.draft(mode=mode)
                self.assertEqual(generation['status'], 'failed')
                self.assertEqual(len([c for c in self.calls(generation['id']) if c['step'] == 'rubric_draft:q18']), 2)
                self.assertEqual(self.uploaded_questions(), [])
        self.host.script = lambda body, host: survey_answer(('18',)) if prompt_name(body) == 'rubric_survey' else write_answer(payload_of(body))
        generation = self.draft(mode='negative')
        draft = generation['result']['questions'][0]
        for points in (1, -11):
            result = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], {'request_id': str(uuid.uuid4()),
                'questions': [self.keep(draft, items=[{'description': 'Invalid deduction', 'points': points}])]})
            self.assertEqual(result['results'][0]['status'], 'failed')
        self.assertEqual(self.uploaded_questions(), [])

    def test_existing_generation_without_mode_replays_and_accepts_as_positive(self):
        slot = self.frq.assignment_templates(self.plan['id'])['slots'][0]
        command = {'key': slot['key'], 'template_id': self.blank['id'], 'request_id': str(uuid.uuid4())}
        queued = self.frq.queue_rubric_generation(self.plan['id'], command)
        with connect(self.path) as db:
            old = dict(db.execute('SELECT * FROM frq_rubric_generations WHERE id=?', (queued['id'],)).fetchone())
        self.frq.cancel_rubric_generation(self.plan['id'], queued['id'])
        # Insert a retained pre-change request without altering the immutable inputs of any existing row.
        context, endpoint = json.loads(old['input_json']), json.loads(old['profile_json'])['endpoint']
        context.pop('mode')
        context['rule'] = 'rubric-draft-v2'
        old.update(id=str(uuid.uuid4()), request_id=str(uuid.uuid4()), input_json=encoded(context), request_sha256=digest([self.plan['id'], context, endpoint]))
        with connect(self.path) as db:
            db.execute(f"INSERT INTO frq_rubric_generations({','.join(old)}) VALUES({','.join('?' for _ in old)})", tuple(old.values()))
        retry = {**command, 'request_id': old['request_id']}
        self.assertEqual(self.frq.queue_rubric_generation(self.plan['id'], retry)['id'], old['id'])
        with self.assertRaises(Conflict):
            self.frq.queue_rubric_generation(self.plan['id'], {**retry, 'mode': 'negative'})
        self.frq.process_cloud()
        generation = self.frq.rubric_generation(self.plan['id'], old['id'])
        self.assertEqual((generation['status'], generation['mode']), ('complete', 'positive'))
        self.assertEqual(generation['result']['rule'], 'rubric-draft-v2')
        self.assertTrue(all('mode' not in payload_of(r['body']) for r in self.host.requests))
        result = self.frq.accept_rubric_generation(self.plan['id'], old['id'], {'request_id': str(uuid.uuid4()),
            'questions': [self.keep(generation['result']['questions'][0])]})
        self.assertEqual(result['results'][0]['status'], 'imported')
        [question] = self.uploaded_questions()
        self.assertEqual(question['rubric']['rules']['mode'], 'positive')
        self.assertEqual(question['rubric']['compiler']['rule'], 'rubric-draft-v2')

    def test_refused_answers_are_asked_once_more_and_a_problem_that_fails_is_listed(self):
        seen = {'survey': 0}
        def script(body, host):
            name, payload = prompt_name(body), payload_of(body)
            if name == 'rubric_survey':
                seen['survey'] += 1
                return survey_answer(('18', '18')) if seen['survey'] == 1 else survey_answer(('18', '19'))
            if payload['problem']['number'] == '19':
                return {**write_answer(payload), 'points': 12}  # Not the printed 10 points: refused, asked again, refused again.
            return write_answer(payload)
        self.host.script = script
        generation = self.draft()
        self.assertEqual(generation['status'], 'complete')
        self.assertEqual([q['number'] for q in generation['result']['questions']], ['18'])
        self.assertEqual(generation['result']['failed'][0]['number'], '19')
        self.assertIn('prints 10', generation['result']['failed'][0]['error'])
        steps = [(c['step'], c['task_attempt']) for c in self.calls(generation['id'])]
        self.assertEqual(steps.count(('rubric_draft:survey', 0)) + steps.count(('rubric_draft:survey', 1)), 2)
        self.assertEqual(sorted(s for s in steps if s[0] == 'rubric_draft:q19'), [('rubric_draft:q19', 0), ('rubric_draft:q19', 1)])
        reask = [r['body'] for r in self.host.requests if prompt_name(r['body']) == 'rubric_survey'][1]
        self.assertGreater(reask['temperature'], 0)

    def test_a_draft_without_printed_points_is_flagged_and_the_instructor_sets_them(self):
        self.host.script = lambda body, host: ({**survey_answer(('7',)), 'problems': [{**survey_answer(('7',))['problems'][0], 'points': None, 'points_text': ''}]}
                                               if prompt_name(body) == 'rubric_survey' else {**write_answer(payload_of(body)), 'points': 6,
                                               'items': [{'description': 'Correct answer with supporting work: $$x = 6$$', 'points': 6},
                                                         {'description': 'Sets up the equation', 'points': 2}]})
        generation = self.draft()
        [draft] = generation['result']['questions']
        self.assertEqual((draft['printed_points'], draft['points']), (None, 6))
        self.assertEqual(len(draft['warnings']), 3)  # No printed points, steps short of the total, and no no-credit line.
        result = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], {'request_id': str(uuid.uuid4()),
            'questions': [self.keep(draft, max_points=8, items=[*draft['items'], {'description': 'No answer or completely incorrect', 'points': 0}, {'description': '', 'points': 3}])]})
        self.assertEqual(result['results'][0]['max_points'], 8)
        self.assertEqual([i['points'] for i in self.uploaded_questions()[0]['rubric']['rules']['items']], [6, 2, 0])  # A cleared line is removed.
        refused = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], {'request_id': str(uuid.uuid4()),
            'questions': [self.keep(draft, printed_number='8', max_points=4)]})
        self.assertEqual(refused['results'][0]['status'], 'failed')  # A 6-point line cannot belong to a 4-point question.

    def test_any_assignment_homework_headings_without_points_and_open_responses(self):
        homework = {'problems': [{'number': str(n), 'label': heading, 'points': None, 'points_text': '', 'pages': [n], 'parts': [],
                                  'statement': statement} for n, heading, statement in (
            (1, 'Exercise 2.3', 'Explain why every linear system has zero, one or infinitely many solutions.'),
            (2, 'Exercise 2.4', 'Solve $$3x - 8 = 10$$.'))], 'notes': ['Exercises are numbered by their position.']}
        def script(body, host):
            if prompt_name(body) == 'rubric_survey':
                return homework
            payload = payload_of(body)
            if payload['problem']['number'] == '1':
                return {'solution': 'Two distinct solutions give a line of solutions, so there are infinitely many.', 'final_answers': [], 'points': 4,
                        'items': [{'description': 'Complete and correct response: argues from two distinct solutions to infinitely many', 'points': 4},
                                  {'description': 'Considers two distinct solutions', 'points': 2},
                                  {'description': 'Shows every point between them is a solution', 'points': 2},
                                  {'description': 'No answer or completely incorrect', 'points': 0}], 'notes': 'No points are printed; 4 suits one argument.'}
            return {**write_answer(payload), 'points': 2, 'items': [{'description': 'Correct answer with supporting work: $$x = 6$$', 'points': 2},
                    {'description': 'Adds 8 to both sides', 'points': 1}, {'description': 'Divides by 3', 'points': 1},
                    {'description': 'No answer or completely incorrect', 'points': 0}]}
        self.host.script = script
        generation = self.draft()
        explained, solved = generation['result']['questions']
        self.assertEqual([(q['number'], q['title'], q['printed_points'], q['points']) for q in (explained, solved)],
                         [('1', 'Exercise 2.3', None, 4), ('2', 'Exercise 2.4', None, 2)])
        self.assertEqual(generation['result']['notes'], ['Exercises are numbered by their position.'])
        # Only the missing printed points need the instructor's eye: the open response has its full-credit and no-credit lines.
        self.assertEqual(len(explained['warnings']), 1)
        self.assertIn('No points are printed', explained['warnings'][0])
        self.assertEqual(explained['final_answers'], [])
        # Each problem is written with its own page and its printed heading.
        writes = [payload_of(r['body'])['problem'] for r in self.host.requests if prompt_name(r['body']) == 'rubric_write']
        self.assertEqual(sorted((w['number'], w['label']) for w in writes), [('1', 'Exercise 2.3'), ('2', 'Exercise 2.4')])
        result = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], {'request_id': str(uuid.uuid4()),
            'questions': [self.keep(explained, max_points=5, title='Exercise 2.3 (linear systems)', items=[*explained['items'][:3],
                          {'description': 'Gives a clear written conclusion', 'points': 1}, explained['items'][3]]), self.keep(solved)]})
        self.assertEqual([r['status'] for r in result['results']], ['imported', 'imported'])
        titles = {q['content']['printed_number']: (q['content']['title'], q['content']['max_points'], q['rubric']['compiler']['edited']) for q in self.uploaded_questions()}
        self.assertEqual(titles, {'1': ('Exercise 2.3 (linear systems)', 5, True), '2': ('Exercise 2.4', 2, False)})
        # An open response has no answer to propose as a key; the solved exercise does.
        keys = {q['content']['printed_number']: q['answer']['proposed_key'] for q in self.uploaded_questions()}
        self.assertEqual(keys, {'1': [], '2': [{'label': '', 'accept': ['x = 6']}]})

    def test_guards_cancellation_and_a_wait_that_sends_nothing_twice(self):
        slot = self.frq.assignment_templates(self.plan['id'])['slots'][0]
        with self.assertRaisesRegex(ValueError, 'blank template'):
            self.frq.queue_rubric_generation(self.plan['id'], {'key': slot['key'], 'template_id': 'nope', 'request_id': str(uuid.uuid4())})
        self.configure('beta-text')
        with self.assertRaisesRegex(ValueError, 'cannot read images'):
            self.frq.queue_rubric_generation(self.plan['id'], {'key': slot['key'], 'template_id': self.blank['id'], 'request_id': str(uuid.uuid4())})
        self.configure('alpha-vision')
        queued = self.frq.queue_rubric_generation(self.plan['id'], {'key': slot['key'], 'template_id': self.blank['id'], 'request_id': str(uuid.uuid4())})
        with self.assertRaises(Conflict):
            self.frq.queue_rubric_generation(self.plan['id'], {'key': slot['key'], 'template_id': self.blank['id'], 'request_id': str(uuid.uuid4())})
        self.assertEqual(self.frq.cancel_rubric_generation(self.plan['id'], queued['id'])['status'], 'cancelled')
        self.frq.process_cloud()
        self.assertEqual(self.host.requests, [])
        with self.assertRaises(Conflict):
            self.frq.accept_rubric_generation(self.plan['id'], queued['id'], {'request_id': str(uuid.uuid4()), 'questions': [{'draft': '18'}]})
        # A host that asks to wait: the draft waits, and the survey it already answered is replayed from its receipt, never resent.
        busy = {'left': 1}
        def script(body, host):
            if prompt_name(body) == 'rubric_survey':
                return survey_answer(('18',))
            if busy['left']:
                busy['left'] -= 1
                return ('status', 429, {'Retry-After': '30'}, {'error': {'message': 'busy'}})
            return write_answer(payload_of(body))
        self.host.script = script
        queued = self.frq.queue_rubric_generation(self.plan['id'], {'key': slot['key'], 'template_id': self.blank['id'], 'request_id': str(uuid.uuid4())})
        self.frq.process_cloud()
        waiting = self.frq.rubric_generation(self.plan['id'], queued['id'])
        self.assertEqual((waiting['status'], waiting['progress']['surveyed'], waiting['progress']['total']), ('pending', True, 1))
        with connect(self.path) as db:
            db.execute("UPDATE frq_rubric_generations SET not_before='' WHERE id=?", (queued['id'],))
        self.frq.process_cloud()
        done = self.frq.rubric_generation(self.plan['id'], queued['id'])
        self.assertEqual(done['status'], 'complete')
        self.assertEqual([prompt_name(r['body']) for r in self.host.requests], ['rubric_survey', 'rubric_write', 'rubric_write'])
        with connect(self.path) as db:
            with self.assertRaises(Exception):
                db.execute("UPDATE frq_rubric_generations SET result_json='{}' WHERE id=?", (queued['id'],))


class AnswerKeysFromTheBlankExam(unittest.TestCase):
    """requirement, 2026-09-25: where nothing can be read from a rubric's full-credit line (a matrix written after M= in words), the model
    reads the key from the printed problem; it is shown for the instructor to save, and saved only by them."""
    # The drafts' fixture without their tests: a plan, a blank template and a loopback model host.
    managed = False
    document_version, ready_batch, reviewed_source = RubricDrafts.document_version, RubricDrafts.ready_batch, RubricDrafts.reviewed_source
    upload, uploaded_questions, template, configure = RubricDrafts.upload, RubricDrafts.uploaded_questions, RubricDrafts.template, RubricDrafts.configure
    MATRIX = '\\begin{bmatrix} 20 & 35 \\\\ 2 & 1 \\end{bmatrix}'

    def setUp(self):
        RubricDrafts.setUp(self)
        self.sent = []
        def model(body, host):
            if prompt_name(body) != 'answer_key_read':
                return survey_answer(('18',)) if prompt_name(body) == 'rubric_survey' else write_answer(payload_of(body))
            self.sent.append(payload_of(body))
            return self.answer
        self.answer = {'parts': [{'label': 'a', 'accept': [self.MATRIX], 'basis': 'Rows silver, gold; columns Cal, Anna.'},
                                 {'label': 'b', 'accept': ['6130.10'], 'basis': 'As the full-credit line states it.'}], 'unreadable': ''}
        self.host.script = model
        self.upload()
        [q] = self.uploaded_questions()
        self.qid = q['question_id']
        with connect(self.path) as db:  # Its confirmed blank exam, as Answer areas leaves it.
            self.frq._set_location_template(db, self.plan['id'], self.frq.question(db, self.plan['id'], self.qid), self.blank['id'], keep_regions=True)
        plan = self.frq.assignment(self.plan['id'])
        q = next(q for q in plan['questions'] if q['question_id'] == self.qid)
        self.frq.save_answer_key(self.plan['id'], {'version': plan['version'], 'questions': [{'question_id': self.qid, 'version': q['version'], 'answer_key': [],
            'problem': 'Cal has 20 ounces of silver and 2 of gold; Anna has 35 of silver and 1 of gold. a. Write M. b. How much money does Anna have?'}]})

    def question(self):
        return next(q for q in self.frq.assignment(self.plan['id'])['questions'] if q['question_id'] == self.qid)

    def read(self, request_id=None):
        return self.frq.queue_answer_key_reading(self.plan['id'], {'request_id': request_id or str(uuid.uuid4()), 'question_ids': [self.qid]})

    def test_a_key_the_rubric_cannot_give_is_read_from_the_printed_problem_and_saved_only_by_the_instructor(self):
        self.assertEqual(self.question()['answer']['proposed_key'], [])  # "Full credit" names no answer.
        queued = self.read('keys-1')
        self.assertEqual(next(q for q in queued['questions'] if q['question_id'] == self.qid)['answer']['read_key']['status'], 'pending')
        self.read('keys-1')  # The same request is the same reading.
        self.frq.process_cloud()
        [payload] = self.sent
        self.assertEqual(sorted(payload), ['full_credit_line', 'pictures', 'problem_text', 'question', 'rubric_lines'])
        self.assertIn('Cal has 20 ounces', payload['problem_text'])
        self.assertEqual(payload['full_credit_line'], 'R1_1')
        reading = self.question()['answer']['read_key']
        self.assertEqual((reading['status'], reading['current'], reading['model']), ('complete', True, 'alpha-vision'))
        self.assertEqual(reading['key'], [{'label': 'a', 'accept': [self.MATRIX]}, {'label': 'b', 'accept': ['6130.10']}])
        self.assertEqual(reading['basis'][0], 'Rows silver, gold; columns Cal, Anna.')
        # Shown, never saved on its own, and never a draft.
        self.assertEqual(self.question()['content'].get('answer_key'), [])
        self.assertEqual(self.frq.assignment_templates(self.plan['id'])['generations'], [])
        with self.assertRaisesRegex(Conflict, 'saved with the answer key'):
            self.frq.accept_rubric_generation(self.plan['id'], reading['id'], {'request_id': 'x', 'questions': [{'draft': '1'}]})
        # Saving it is the instructor's ordinary answer-key save; then the question has a key to edit, not to read.
        q, plan = self.question(), self.frq.assignment(self.plan['id'])
        self.frq.save_answer_key(self.plan['id'], {'version': plan['version'], 'questions': [{'question_id': self.qid, 'version': q['version'], 'answer_key': reading['key']}]})
        self.assertEqual(self.question()['content']['answer_key'], reading['key'])
        with self.assertRaisesRegex(Conflict, 'already has an answer key to check'):
            self.read()

    def test_a_reading_is_sent_the_questions_worked_solution_and_an_uploaded_question_still_starts_without_a_key(self):
        """answer_key_read v2 (requirement, 2026-10-11): the plan's own reading adds the question's stored worked
        solution, to be used instead of solving the problem again; a question without one is asked exactly as before (the test
        above). A request queued under the earlier rule and sent again afterwards is still that reading."""
        from unittest.mock import patch
        from grading import rubric_generation
        # An uploaded CSV's question is created without a key, as it always was: only an approved draft's question starts with one.
        with connect(self.path) as db:
            first = json.loads(db.execute('SELECT content_json FROM frq_question_revisions WHERE question_id=? AND revision=1', (self.qid,)).fetchone()[0])
        self.assertEqual((first['answer_key'], first['worked_solution'], first['problem']), ([], '', ''))

        def save(**changed):
            q, plan = self.question(), self.frq.assignment(self.plan['id'])
            self.frq.save_answer_key(self.plan['id'], {'version': plan['version'], 'questions': [{'question_id': self.qid, 'version': q['version'], 'answer_key': [], **changed}]})
        with patch.object(rubric_generation, 'KEY_RULE', 'answer-key-read-v1'):  # queued before the rule changed: no solution was sent
            self.read('keys-old')
        save(worked_solution='M has a row for each metal and a column for each person. Anna: 35(170.86)+1(150)=6130.10.')
        self.read('keys-old')  # the same request again, under v2 and with a solution now stored: the same reading, nothing new queued
        self.frq.process_cloud()
        self.assertEqual(sorted(self.sent[0]), ['full_credit_line', 'pictures', 'problem_text', 'question', 'rubric_lines'])  # as it was queued
        # A reading asked for now carries the solution, under the v2 rule; what it reads is still shown, never saved on its own.
        self.read('keys-new')
        self.frq.process_cloud()
        self.assertEqual(sorted(self.sent[1]), ['full_credit_line', 'pictures', 'problem_text', 'question', 'rubric_lines', 'worked_solution'])
        self.assertIn('35(170.86)+1(150)=6130.10', self.sent[1]['worked_solution'])
        with connect(self.path) as db:
            rules = [json.loads(r[0])['rule'] for r in db.execute('SELECT input_json FROM frq_rubric_generations WHERE assignment_id=? ORDER BY rowid', (self.plan['id'],))]
        self.assertEqual(rules, ['answer-key-read-v1', 'answer-key-read-v2'])
        reading = self.question()['answer']['read_key']
        self.assertEqual((reading['status'], reading['current'], self.question()['content']['answer_key']), ('complete', True, []))
        # The same request ID for a question that reads otherwise now is another reading, whichever rule it was queued under.
        save(problem='Cal has 21 ounces of silver.')
        for request_id in ('keys-old', 'keys-new'):
            with self.assertRaisesRegex(Conflict, 'belongs to another reading'):
                self.read(request_id)

    def test_a_reading_names_what_it_could_not_read_and_an_older_rubric_is_not_current(self):
        self.answer = {'parts': [], 'unreadable': 'The prices are cut off on the blank exam.'}
        self.read()
        self.frq.process_cloud()
        reading = self.question()['answer']['read_key']
        self.assertEqual((reading['status'], reading['key'], reading['unreadable']), ('complete', [], 'The prices are cut off on the blank exam.'))
        q = self.question()
        self.frq.import_rubric(self.plan['id'], self.qid, {'version': q['version'], 'source_text': 'New revision', 'rules': q['rubric']['rules']})
        self.assertFalse(self.question()['answer']['read_key']['current'])  # Read under the earlier rubric: not shown as its key.

    def test_a_question_whose_rubric_gives_a_key_or_that_has_no_blank_exam_is_not_read(self):
        from test_gradescope_rubrics import encode, export_rows
        self.upload('1_Question_19.csv', encode(export_rows(descriptions=['Correct Answer: $$x=4$$', 'Setup', 'Arithmetic', 'No answer'])).encode())
        keyed = next(q for q in self.uploaded_questions() if q['question_id'] != self.qid)
        with self.assertRaisesRegex(Conflict, 'already has an answer key to check'):
            self.frq.queue_answer_key_reading(self.plan['id'], {'request_id': str(uuid.uuid4()), 'question_ids': [keyed['question_id']]})
        self.upload('1_Question_20.csv')
        bare = next(q for q in self.uploaded_questions() if q['question_id'] not in (self.qid, keyed['question_id']))
        with self.assertRaisesRegex(Conflict, 'no confirmed answer area'):
            self.frq.queue_answer_key_reading(self.plan['id'], {'request_id': str(uuid.uuid4()), 'question_ids': [bare['question_id']]})
        self.assertEqual(self.sent, [])


class AcceptingADraftReadiesIt(unittest.TestCase):
    """ACCEPTING A DRAFT READIES IT (requirement, 2026-10-11: they read the same drafted rubric three times, to accept it, to confirm it for
    review and to finalize it). Accepting is the confirmation for review of the questions it creates; each one's key is read without
    being asked once its answer area is confirmed, as the button reads it (the picture of that area, the problem, the worked
    solution), and stays a proposal; and the rubric check is asked for by itself once the plan is ready, never before and once."""
    managed = False
    document_version, ready_batch, reviewed_source = RubricDrafts.document_version, RubricDrafts.ready_batch, RubricDrafts.reviewed_source
    upload, uploaded_questions, template, configure = RubricDrafts.upload, RubricDrafts.uploaded_questions, RubricDrafts.template, RubricDrafts.configure
    draft, keep = RubricDrafts.draft, RubricDrafts.keep
    KEY = {'parts': [{'label': '', 'accept': ['x = 6'], 'basis': 'As the worked solution ends.'}], 'unreadable': ''}
    OPEN = {'solution': 'M has a row for each metal and a column for each person.', 'final_answers': [], 'points': 10, 'notes': '',
            'items': [{'description': 'Complete and correct matrix with its labels', 'points': 10}, {'description': 'Rows correct', 'points': 5},
                      {'description': 'Columns correct', 'points': 5}, {'description': 'No answer or completely incorrect', 'points': 0}]}

    def setUp(self):
        RubricDrafts.setUp(self)
        self.aid, self.numbers, self.sent, self.pictures, self.keys, self.written = self.plan['id'], ('18',), [], [], {}, {}
        def model(body, host):
            name, payload = prompt_name(body), payload_of(body)
            if name == 'rubric_survey':
                return survey_answer(self.numbers)
            if name == 'answer_key_read':
                self.sent.append(payload)
                self.pictures.append(len(images_of(body)))
                return self.keys.get(payload['question']['printed_number'], self.KEY)
            return self.written.get(payload['problem']['number']) or write_answer(payload)
        self.host.script = model

    def accept(self, generation, request_id=None):
        return self.frq.accept_rubric_generation(self.aid, generation['id'], {'request_id': request_id or str(uuid.uuid4()),
            'questions': [self.keep(q, mode=generation['mode']) for q in generation['result']['questions']]})

    def question(self, qid):
        return next(q for q in self.uploaded_questions() if q['question_id'] == qid)

    def written_rows(self):
        """Every table this may write to, row for row: a wait, a refusal and a request sent again leave all of them as they were."""
        with connect(self.path) as db:
            return {table: [tuple(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY rowid')] for table in (
                'frq_events', 'frq_rubric_review_ready', 'frq_rubric_generations', 'frq_rubric_quality_checks', 'frq_rubric_quality_options',
                'frq_rubric_line_comments', 'frq_question_revisions', 'frq_rubric_revisions', 'frq_rubric_uploads', 'frq_assignments')}

    def confirmed_by(self):
        with connect(self.path) as db:
            return [(r['question_id'], r['actor']) for r in db.execute('SELECT question_id,actor FROM frq_rubric_review_ready ORDER BY rowid')]

    def ready(self):
        return {qid: state['ready'] for qid, state in self.frq.rubric_quality(self.aid)['readiness'].items()}

    def checks(self):
        with connect(self.path) as db:
            return [dict(r) for r in db.execute('SELECT * FROM frq_rubric_quality_checks WHERE assignment_id=? ORDER BY rowid', (self.aid,))]

    def readings(self):
        """The plan's answer-key readings, with the answer-area confirmation each question's latest area was confirmed by."""
        with connect(self.path) as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM frq_rubric_generations WHERE assignment_id=? AND json_extract(input_json,'$.kind')='key' ORDER BY rowid", (self.aid,))]
            for row in rows:
                row['input'] = json.loads(row['input_json'])
                row['confirmation'] = db.execute('SELECT id FROM frq_location_reviews WHERE assignment_id=? AND question_id=? ORDER BY rowid DESC LIMIT 1',
                                                 (self.aid, row['input']['question_id'])).fetchone()[0]
        return rows

    def waits(self):
        """What the rubric check waits for, said without writing anything; while it waits the plan read says the same, read only."""
        before = self.written_rows()
        said = self.frq.request_rubric_review_when_ready(self.aid)
        self.assertEqual(self.written_rows(), before)
        plan = self.frq.assignment(self.aid)
        self.assertEqual(self.written_rows(), before)
        self.assertEqual(plan.get('rubric_review_waits'), said if said.startswith('The rubric check starts by itself once') else None)
        return said

    def suggested_areas(self):
        """Assignment Templates confirmed and the blank read: every question's answer area is suggested, for the instructor to confirm."""
        confirm_templates(self.frq, self.aid)
        for _ in range(60):
            self.frq.process_location_one()
            questions = self.uploaded_questions()
            if all(q['location_check']['status'] in ('needs_confirmation', 'needs_review', 'failed') for q in questions):
                self.assertEqual({q['location_check']['status'] for q in questions}, {'needs_confirmation'})
                return {q['content']['printed_number']: q for q in questions}
        self.fail('The answer areas were not read')

    def confirm_area(self, q):
        check = q['location_check']
        return self.frq.confirm_location(self.aid, q['question_id'], {'version': q['version'], 'check_id': check['id'], 'input_sha256': check['input_sha256'],
            'template_id': self.blank['id'], 'printed_number': q['content']['printed_number'], 'regions': check['result']['regions'], 'confirm_locations': True})

    def save_key(self, qid, key):
        q, plan = self.question(qid), self.frq.assignment(self.aid)
        return self.frq.save_answer_key(self.aid, {'version': plan['version'], 'questions': [{'question_id': qid, 'version': q['version'], 'answer_key': key}]})

    def test_accepting_confirms_what_it_created_an_upload_stays_yours_and_an_edit_reopens_only_its_question(self):
        self.numbers = ('18', '19')
        self.upload('1_Question_20.csv')
        [uploaded] = self.uploaded_questions()
        generation = self.draft()
        self.assertEqual(self.confirmed_by(), [])  # A draft confirms nothing.
        result = self.accept(generation, 'accept-1')
        made = {r['draft']: r['question_id'] for r in result['results']}
        self.assertEqual(([r['status'] for r in result['results']], result['readied']), (['imported', 'imported'], [made['18'], made['19']]))
        self.assertEqual(self.confirmed_by(), [(made['18'], 'draft-acceptance'), (made['19'], 'draft-acceptance')])
        status = self.frq.rubric_quality(self.aid)
        # The uploaded CSV keeps the explicit confirmation, and each confirmation names the drafted clarifications saved as comments.
        self.assertEqual(self.ready(), {made['18']: True, made['19']: True, uploaded['question_id']: False})
        self.assertEqual(status['unconfirmed'], [uploaded['content']['title']])
        self.assertEqual(sorted(status['readiness'][made['18']]['comments']), ['R2', 'R4'])
        self.assertEqual(self.question(made['18'])['rubric']['approved_at'], None)  # Nothing is approved: the finalization still does that.
        # These rubrics name their answer, so the key is the rubric's proposal and none is to be read; the check waits, and says for what.
        self.assertEqual(result['keys_to_read'], [])
        self.assertEqual(result['review'], 'The rubric check starts by itself once every rubric is confirmed for review, the answer areas are confirmed and the answer keys are saved.')
        self.assertEqual((self.waits(), self.checks(), self.readings()), (result['review'], [], []))
        # The same request again writes nothing more.
        before = self.written_rows()
        again = self.accept(generation, 'accept-1')
        self.assertEqual(({r['status'] for r in again['results']}, again['readied'], again['keys_to_read']), ({'already_imported'}, [], []))
        self.assertEqual(self.written_rows(), before)
        # A comment reopens exactly its question, and so does a changed line; the others stay confirmed.
        q = self.question(made['18'])
        self.frq.edit_rubric(self.aid, made['18'], {'version': q['version'], 'rubric_id': q['rubric_id'], 'sha256': q['rubric']['sha256'],
                                                    'comments': {'R1': 'Accept the answer boxed or underlined.'}})
        self.assertEqual(self.ready(), {made['18']: False, made['19']: True, uploaded['question_id']: False})
        confirm_for_review(self.frq, self.aid)  # The page's own button: theirs, for the uploaded rubric too.
        self.assertEqual(sorted(self.confirmed_by()[2:]), sorted([(made['18'], 'workspace'), (uploaded['question_id'], 'workspace')]))
        q = self.question(made['19'])
        self.frq.edit_rubric(self.aid, made['19'], {'version': q['version'], 'rubric_id': q['rubric_id'], 'sha256': q['rubric']['sha256'],
                                                    'lines': [{'item_id': 'R2', 'description': 'Reworded by the instructor'}]})
        self.assertEqual(self.ready(), {made['18']: True, made['19']: False, uploaded['question_id']: True})
        # Sent once more, the acceptance confirms nothing the instructor changed since.
        before = self.written_rows()
        self.assertEqual(self.accept(generation, 'accept-1')['readied'], [])
        self.assertEqual(self.written_rows(), before)

    def test_an_acceptance_never_fails_and_sent_again_it_finishes_only_what_is_still_as_it_left_it(self):
        self.numbers = ('18', '19')
        generation = self.draft()
        with patch.object(self.frq, 'confirm_rubric_review', side_effect=Conflict('A rubric or its comments changed.')):
            result = self.accept(generation, 'accept-1')
        made = {r['draft']: r['question_id'] for r in result['results']}
        self.assertEqual(([r['status'] for r in result['results']], result['readied'], self.confirmed_by()), (['imported', 'imported'], [], []))
        self.assertEqual(result['review'], 'The rubric check of these rubrics is started in Key & Rubric Finalization.')
        self.assertEqual(self.ready(), {made['18']: False, made['19']: False})  # The page's own button still confirms them.
        q = self.question(made['19'])
        self.frq.edit_rubric(self.aid, made['19'], {'version': q['version'], 'rubric_id': q['rubric_id'], 'sha256': q['rubric']['sha256'],
                                                    'comments': {'R1': 'Written after the draft was accepted.'}})
        again = self.accept(generation, 'accept-1')
        self.assertEqual(({r['status'] for r in again['results']}, again['readied']), ({'already_imported'}, [made['18']]))
        self.assertEqual(self.confirmed_by(), [(made['18'], 'draft-acceptance')])

    def test_the_key_is_read_once_its_answer_area_is_confirmed_as_the_button_reads_it_and_stays_a_proposal(self):
        self.numbers, self.written, self.reader.extra = ('18', '19'), {'19': self.OPEN}, [('19', 10)]
        generation = self.draft()
        result = self.accept(generation, 'accept-1')
        made = {r['draft']: r['question_id'] for r in result['results']}
        self.assertEqual(self.question(made['18'])['answer']['proposed_key'], [{'label': '', 'accept': ['x = 6']}])
        # NOTHING is read at acceptance (no picture of the problem exists yet): the answer names the question whose rubric proposes no
        # key, to be read once its answer area is confirmed, and the button's own request is refused until then, as it always was.
        self.assertEqual((self.question(made['19'])['answer']['proposed_key'], result['keys_to_read'], self.readings()), ([], [made['19']], []))
        self.assertEqual(result['review'], 'The rubric check starts by itself once the answer areas are confirmed and the answer keys, each read once its answer area is confirmed, are saved.')
        with self.assertRaisesRegex(Conflict, 'no confirmed answer area'):
            self.frq.queue_answer_key_reading(self.aid, {'request_id': str(uuid.uuid4()), 'question_ids': [made['19']]})
        before = self.written_rows()
        self.assertEqual(self.accept(generation, 'accept-1')['keys_to_read'], [made['19']])  # The same request again says the same and writes nothing.
        self.assertEqual(self.written_rows(), before)
        areas = self.suggested_areas()
        self.confirm_area(areas['18'])
        self.assertEqual(self.readings(), [])  # Question 18's rubric proposes its key: none is read for it.
        confirmed = self.confirm_area(areas['19'])
        # One ORDINARY reading, named by that confirmation and the question, of the area just confirmed on the question's blank.
        [reading] = self.readings()
        with connect(self.path) as db:
            [event] = [dict(r) for r in db.execute("SELECT * FROM frq_events WHERE kind='answer_key_reading_queued'")]
        self.assertEqual((reading['request_id'], reading['template_id'], reading['status']), (f"area-confirmed:{reading['confirmation']}:{made['19']}", self.blank['id'], 'pending'))
        self.assertEqual((event['actor'], json.loads(event['detail_json'])['question_ids']), ('automatic', [made['19']]))
        q = self.question(made['19'])
        self.assertEqual(reading['input']['printed'], [{'template_id': self.blank['id'], 'page': area['page'], 'region': area['region']} for area in q['content']['regions']])
        self.assertEqual(q['answer']['read_key']['status'], 'pending')
        # That was the last answer area, yet the check waits for the reading it just asked for, and the plan read says so.
        self.assertEqual((confirmed['rubric_quality']['state'], confirmed['rubric_review_waits'], self.checks()),
                         ('needed', 'The rubric check starts by itself once the answer keys are read and saved.', []))
        self.frq.process_cloud()
        # Sent what the button's reading is sent: the picture of the confirmed area, the problem's text and the stored worked solution.
        [payload] = self.sent
        self.assertEqual(sorted(payload), ['full_credit_line', 'pictures', 'problem_text', 'question', 'rubric_lines', 'worked_solution'])
        self.assertEqual((payload['pictures'], self.pictures, payload['question']['printed_number'], payload['worked_solution']), (1, [1], '19', self.OPEN['solution']))
        self.assertIn('Solve $$3x - 8 = 10$$', payload['problem_text'])
        # Shown as any reading is, and unused until the instructor saves it; the check waits for both proposals to be saved.
        q = self.question(made['19'])
        self.assertEqual((q['answer']['read_key']['status'], q['answer']['read_key']['current'], q['answer']['read_key']['key']),
                         ('complete', True, [{'label': '', 'accept': ['x = 6']}]))
        self.assertEqual((q['content']['answer_key'], q['answer']['key']), ([], []))
        self.assertNotIn('read_key', self.question(made['18'])['answer'])
        self.assertEqual((self.waits(), self.checks()), ('The rubric check starts by itself once the answer keys are saved.', []))
        self.save_key(made['19'], q['answer']['read_key']['key'])
        self.assertEqual((self.question(made['19'])['content']['answer_key'], self.checks()), ([{'label': '', 'accept': ['x = 6']}], []))
        self.save_key(made['18'], self.question(made['18'])['answer']['proposed_key'])
        self.assertEqual((len(self.checks()), len(self.readings())), (1, 1))

    def test_the_check_waits_for_each_thing_writing_nothing_and_the_saved_key_starts_exactly_one(self):
        generation = self.draft(mode='negative')  # A deducting rubric names no key: every key is read.
        result = self.accept(generation, 'accept-1')
        [qid] = result['readied']
        self.assertEqual((result['keys_to_read'], self.readings()), ([qid], []))
        # The area is not confirmed, and the key is read only once it is.
        self.assertEqual(result['review'], 'The rubric check starts by itself once the answer areas are confirmed and the answer keys, each read once its answer area is confirmed, are saved.')
        self.assertEqual(self.waits(), result['review'])
        # A rubric draft in progress.
        slot = self.frq.assignment_templates(self.aid)['slots'][0]
        drafting = self.frq.queue_rubric_generation(self.aid, {'key': slot['key'], 'template_id': self.blank['id'], 'request_id': str(uuid.uuid4())})
        self.assertEqual(self.waits(), 'The rubric check starts by itself once the rubric draft in progress has finished, the answer areas are confirmed '
                                       'and the answer keys, each read once its answer area is confirmed, are saved.')
        self.frq.cancel_rubric_generation(self.aid, drafting['id'])
        # The area confirmed: its key is being read, and the check waits for that reading though nothing else is missing.
        confirmed = self.confirm_area(self.suggested_areas()['18'])
        self.assertEqual((confirmed['rubric_quality']['state'], [r['status'] for r in self.readings()]), ('needed', ['pending']))
        self.assertEqual((self.waits(), self.checks()), ('The rubric check starts by itself once the answer keys are read and saved.', []))
        # The key was read: a proposal that waits to be saved. A reading that finishes with a key starts nothing.
        self.frq.process_cloud()
        reading = self.question(qid)['answer']['read_key']
        self.assertEqual((reading['status'], reading['key'], self.pictures), ('complete', [{'label': '', 'accept': ['x = 6']}], [1]))
        self.assertEqual(self.waits(), 'The rubric check starts by itself once the answer keys are saved.')
        self.assertEqual(self.checks(), [])
        # Saving the key is the last thing: exactly one check, named by the plan and its rubrics, with the key and the printed problem.
        saved = self.save_key(qid, reading['key'])
        self.assertEqual(saved['rubric_quality']['state'], 'pending')
        [check] = self.checks()
        self.assertEqual((check['request_id'], check['status']), (f"draft-review:{self.aid}:{check['input_sha256'][:40]}", 'pending'))
        [asked] = json.loads(check['input_json'])['questions']
        reference, q = asked['reference'], self.question(qid)
        self.assertEqual((asked['question_id'], reference['answer_key'], reference['problem'], reference['worked_solution']),
                         (qid, reading['key'], q['content']['problem'], q['content']['worked_solution']))
        self.assertEqual([(area['template_id'], area['page'], area['region']) for area in reference['printed']],
                         [(self.blank['id'], area['page'], area['region']) for area in q['content']['regions']])
        self.assertTrue(reference['printed'])
        self.assertEqual(self.confirmed_by(), [(qid, 'draft-acceptance')])  # The finalization itself is still the instructor's.
        self.assertIsNone(self.frq.rubric_quality(self.aid)['review'])
        # A check that is there, running or not, is never asked for again by itself.
        self.assertEqual(self.waits(), 'A rubric check is already waiting or running.')
        self.frq.cancel_rubric_quality(self.aid, {})
        self.assertEqual(self.waits(), 'These rubrics already have a rubric check: review it or run it again in Key & Rubric Finalization.')
        self.save_key(qid, [{'label': '', 'accept': ['x = 6', '6']}])  # A corrected key is no new rubric: nothing starts.
        self.assertEqual(len(self.checks()), 1)

    def test_the_last_answer_area_never_starts_the_check_ahead_of_its_key_and_a_reading_that_finds_none_starts_it_when_it_ends(self):
        self.numbers, self.reader.extra = ('18', '19'), [('19', 10)]
        self.keys = {'19': {'parts': [], 'unreadable': 'The prices are cut off on the blank assignment.'}}
        result = self.accept(self.draft(mode='negative'), 'accept-1')
        first, second = result['readied']
        self.assertEqual((result['keys_to_read'], self.readings()), ([first, second], []))
        areas = self.suggested_areas()
        self.confirm_area(areas['18'])
        self.frq.process_cloud()
        self.save_key(first, self.question(first)['answer']['read_key']['key'])
        # Question 18 is settled; question 19's area still waits, and its key with it.
        self.assertEqual((self.waits(), self.checks()), (
            'The rubric check starts by itself once the answer areas are confirmed and the answer keys, each read once its answer area is confirmed, are saved.', []))
        q = self.question(second)
        confirmed = self.frq.confirm_all_locations(self.aid, {'confirm_locations': True, 'questions': [
            {'question_id': second, 'version': q['version'], 'check_id': q['location_check']['id'], 'input_sha256': q['location_check']['input_sha256']}]})
        # The LAST answer area: its key is asked to be read, and the check is not started ahead of the reading it just asked for.
        self.assertEqual((confirmed['rubric_quality']['state'], confirmed['rubric_review_waits'], [r['status'] for r in self.readings()], self.checks()),
                         ('needed', 'The rubric check starts by itself once the answer keys are read and saved.', ['complete', 'pending'], []))
        # The reading ends without a key, so nothing is left to save: its end asks again, and the check starts, once.
        with patch.object(self.frq, 'run_rubric_quality') as started:
            self.frq.process_cloud()
        self.assertEqual((started.call_count, self.question(second)['answer']['read_key']['unreadable']), (1, 'The prices are cut off on the blank assignment.'))
        [check] = self.checks()
        self.assertTrue(check['request_id'].startswith(f'draft-review:{self.aid}:'))
        references = {q['question_id']: q['reference'] for q in json.loads(check['input_json'])['questions']}
        self.assertEqual((references[first]['answer_key'], references[second]['answer_key']), ([{'label': '', 'accept': ['x = 6']}], []))
        self.assertTrue(references[first]['printed'] and references[second]['printed'])
        self.assertEqual(self.pictures, [1, 1])  # Each key was read with the picture of its own confirmed area.

    def test_your_own_confirmation_of_an_edited_draft_starts_the_check_through_its_route(self):
        self.numbers, self.reader.extra = ('18', '19'), [('19', 10)]
        result = self.accept(self.draft(mode='negative'), 'accept-1')
        first, second = result['readied']
        q = self.question(second)
        self.frq.edit_rubric(self.aid, second, {'version': q['version'], 'rubric_id': q['rubric_id'], 'sha256': q['rubric']['sha256'],
                                                'comments': {'R1': 'Accept the answer boxed or underlined.'}})
        areas = self.suggested_areas()
        self.frq.confirm_all_locations(self.aid, {'confirm_locations': True, 'questions': [
            {'question_id': q['question_id'], 'version': q['version'], 'check_id': q['location_check']['id'], 'input_sha256': q['location_check']['input_sha256']}
            for q in areas.values()]})
        # Only the question whose CURRENT confirmation is the acceptance has its key read by itself; the edited one keeps the button.
        self.assertEqual([r['input']['question_id'] for r in self.readings()], [first])
        self.frq.process_cloud()
        self.save_key(first, self.question(first)['answer']['read_key']['key'])
        self.assertEqual((self.waits(), self.checks()), ('The rubric check starts by itself once every rubric is confirmed for review.', []))
        state = self.frq.rubric_quality(self.aid)['readiness'][second]
        url, body = f'/api/grading/assignments/{self.aid}/rubric-review-ready', {'questions': {second: {'rubric_id': state['rubric_id'], 'comments_sha256': state['comments_sha256']}}}
        answer = self.client.post(url, json=body, headers=upload_fixture.exam_fixture.HEADERS)
        self.assertEqual((answer.status_code, answer.json['rubric_quality']['state']), (200, 'pending'))
        [check] = self.checks()
        self.assertTrue(check['request_id'].startswith(f'draft-review:{self.aid}:'))
        self.assertEqual(self.confirmed_by(), [(first, 'draft-acceptance'), (second, 'draft-acceptance'), (second, 'workspace')])
        # Once every rubric the check would ask is confirmed by the instructor's own button, running it is theirs again.
        self.frq.cancel_rubric_quality(self.aid, {})
        q = self.question(first)
        self.frq.edit_rubric(self.aid, first, {'version': q['version'], 'rubric_id': q['rubric_id'], 'sha256': q['rubric']['sha256'],
                                               'comments': {'R1': 'Accept the answer boxed or underlined.'}})
        confirm_for_review(self.frq, self.aid)
        self.assertEqual((self.waits(), len(self.checks())), ('The rubric check of these rubrics is started in Key & Rubric Finalization.', 1))

    def test_a_plan_of_uploaded_rubrics_never_starts_its_own_check(self):
        self.reader.number = '18'
        self.upload()
        [q] = self.uploaded_questions()
        confirm_for_review(self.frq, self.aid)
        confirmed = self.confirm_area(self.suggested_areas()['18'])
        # Everything a check needs is there ("Full credit" names no key), yet running it is the instructor's, its key keeps the button
        # (none is read by itself), and the plan read says nothing new.
        self.assertEqual((confirmed['rubric_quality']['state'], confirmed['rubric_quality']['unconfirmed'], self.readings()), ('needed', [], []))
        self.assertNotIn('rubric_review_waits', confirmed)
        self.assertEqual(self.waits(), 'The rubric check of these rubrics is started in Key & Rubric Finalization.')
        saved = self.save_key(q['question_id'], [{'label': '', 'accept': ['x = 6']}])
        self.assertNotIn('rubric_review_waits', saved)
        self.assertEqual((self.checks(), self.confirmed_by()), ([], [(q['question_id'], 'workspace')]))


class ManagedRubricDrafts(RubricDrafts):
    managed = True


if __name__ == '__main__':
    unittest.main()
