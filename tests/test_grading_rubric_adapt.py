"""One version's rubrics adapted to another version (requirement, 2026-09-25), so both versions are graded the same way.

Synthetic template pages, a Gradescope CSV for Version A and a loopback model host. The model pairs the versions' problems by what they
ask and rewrites each reference line by its ID; the points and rules stay the reference rubric's, and nothing is created until the
instructor keeps a question."""
import json
import unittest
import uuid
from unittest.mock import patch

from werkzeug.exceptions import Conflict
from course_storage import timestamp
from rosters import connect
from fake_openai import adapt_answer, images_of, match_answer, payload_of, prompt_name, survey_answer
import test_grading_rubric_drafts as drafts_fixture
from grading.rubric_generation import adaptation_warnings, adapted_rules


class RubricAdaptation(unittest.TestCase):
    managed = False
    Drafts = drafts_fixture.RubricDrafts
    document_version, ready_batch, reviewed_source = Drafts.document_version, Drafts.ready_batch, Drafts.reviewed_source
    upload, uploaded_questions, template = Drafts.upload, Drafts.uploaded_questions, Drafts.template
    configure, calls, keep = Drafts.configure, Drafts.calls, Drafts.keep

    def setUp(self):
        self.Drafts.setUp(self)
        with connect(self.path) as db:
            db.execute('INSERT INTO frq_declared_versions VALUES(?,?,?,?,?,?)', (str(uuid.uuid4()), self.plan['id'], 2, 'declared', 'workspace', timestamp()))
        self.upload(exam_version='A')
        [self.reference] = self.uploaded_questions()
        self.pairs = {'5': '18', '18': None}  # Version B prints Version A's problem 18 as problem 5, and a problem A does not have.
        self.drop = ()
        self.host.script = self.answer

    def answer(self, body, host):
        name, payload = prompt_name(body), payload_of(body)
        if name == 'rubric_survey':
            return survey_answer(('5', '18'))
        if name == 'rubric_adapt_match':
            return match_answer(payload, self.pairs)
        return adapt_answer(payload, self.drop)

    def slots(self):
        return {slot['exam_version']: slot for slot in self.frq.assignment_templates(self.plan['id'])['slots']}

    def adapt(self, **extra):
        slots = self.slots()
        command = {'key': slots['B']['key'], 'adapt_from': slots['A']['key'], 'template_id': self.blank['id'], 'request_id': str(uuid.uuid4()), **extra}
        queued = self.frq.queue_rubric_generation(self.plan['id'], command)
        self.assertEqual((queued['status'], queued['kind'], queued['source']['label']), ('pending', 'adapt', 'Version A'))
        self.assertEqual(self.frq.queue_rubric_generation(self.plan['id'], command)['id'], queued['id'])  # The same request is the same adaptation.
        self.frq.process_cloud()
        return self.frq.rubric_generation(self.plan['id'], queued['id'])

    def comment(self, question, item_id, note):
        rubric = question['rubric']
        self.frq.edit_rubric(self.plan['id'], question['question_id'], {'version': question['version'], 'rubric_id': rubric['id'], 'sha256': rubric['sha256'],
                                                                         'comments': {item_id: note}})

    def test_the_versions_are_paired_by_task_and_every_line_keeps_its_id_and_points(self):
        lines = self.reference['rubric']['rules']['items']
        self.comment(self.reference, lines[1]['id'], 'Accept any equivalent fraction.')
        generation = self.adapt()
        self.assertEqual((generation['status'], generation['kind']), ('complete', 'adapt'))
        self.assertEqual(self.uploaded_questions()[0]['question_id'], self.reference['question_id'])
        self.assertEqual(len(self.uploaded_questions()), 1)  # Nothing is created by adapting.
        [draft] = generation['result']['questions']
        self.assertEqual((draft['number'], draft['mode'], draft['points']), ('5', 'positive', 10))
        self.assertEqual([(i['id'], i['points']) for i in draft['items']], [(i['id'], i['points']) for i in lines])
        self.assertEqual(draft['items'][1]['description'], lines[1]['description'].replace('x_', 'y_').strip())
        self.assertEqual((draft['source']['question_id'], draft['source']['rubric_id'], draft['source']['number']),
                         (self.reference['question_id'], self.reference['rubric_id'], '18'))
        self.assertEqual(draft['reference_items'][1]['comment'], 'Accept any equivalent fraction.')
        [unpaired] = generation['result']['failed']
        self.assertEqual(unpaired['number'], '18')
        self.assertIn('No problem of Version A asks the same task', unpaired['error'])
        self.assertEqual(generation['stale'], [])
        # A survey of Version B, one pairing request that sees both versions, then one request per paired problem alone.
        requests = [r['body'] for r in self.host.requests]
        self.assertEqual([prompt_name(b) for b in requests], ['rubric_survey', 'rubric_adapt_match', 'rubric_adapt'])
        match, adapt = payload_of(requests[1]), payload_of(requests[2])
        self.assertTrue(requests[1]['messages'][0]['content'].startswith('You pair the problems of two versions of ONE assignment'))
        self.assertIn('never from printed number or position alone', requests[1]['messages'][0]['content'])
        self.assertEqual([p['number'] for p in match['reference']['problems']], ['18'])
        self.assertEqual(match['reference']['problems'][0]['rubric_lines'], [i['description'] for i in lines])
        self.assertEqual([p['number'] for p in match['target']['problems']], ['5', '18'])
        self.assertEqual(images_of(requests[1]), [])  # Version A has no chosen blank template to show.
        schema = requests[1]['response_format']['json_schema']['schema']['properties']['pairs']
        self.assertEqual((schema['minItems'], schema['items']['properties']['target']), (2, {'enum': ['5', '18']}))
        self.assertEqual(schema['items']['properties']['reference'], {'anyOf': [{'enum': ['18']}, {'type': 'null'}]})
        self.assertEqual((adapt['reference']['number'], adapt['target']['number'], adapt['reference']['version'], adapt['target']['version']), ('18', '5', 'Version A', 'Version B'))
        self.assertEqual(adapt['reference']['rubric']['items'][1]['instructor_comment'], 'Accept any equivalent fraction.')
        self.assertNotIn('question_id', json.dumps(adapt))
        self.assertEqual(len(images_of(requests[2])), 1)
        ids = requests[2]['response_format']['json_schema']['schema']['properties']['items']
        self.assertEqual((ids['minItems'], ids['maxItems'], ids['items']['properties']['id']), (len(lines), len(lines), {'enum': [i['id'] for i in lines]}))
        self.assertNotIn('points', ids['items']['properties'])
        self.assertEqual({c['state'] for c in self.calls(generation['id'])}, {'completed'})
        # The adaptation shows in Version B's template slot, which now defaults to the template it read.
        slots = self.slots()
        self.assertEqual(slots['B']['template_id'], self.blank['id'])
        [latest] = [g for g in self.frq.assignment_templates(self.plan['id'])['generations'] if g['key'] == slots['B']['key']]
        self.assertEqual((latest['kind'], latest['counts']), ('adapt', {'drafted': 1, 'failed': 1}))

    def test_a_kept_question_keeps_the_reference_rules_and_can_take_the_comments(self):
        lines = self.reference['rubric']['rules']['items']
        self.comment(self.reference, lines[1]['id'], 'Accept any equivalent fraction.')
        generation = self.adapt()
        [draft] = generation['result']['questions']
        accepted = [{'source': line['id'], 'description': line['description'], 'points': line['points']} for line in draft['items']]
        accepted[0]['description'] = 'Full credit for $$y = 7$$'
        accepted[2]['description'] = ''  # Cleared: the line is left out.
        accepted.append({'source': '', 'description': 'Units written', 'points': 0})
        command = {'request_id': str(uuid.uuid4()), 'copy_comments': True, 'questions': [
            self.keep(draft, items=accepted, title='Question 5', printed_number='5')]}
        result = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], command)
        [created] = result['results']
        self.assertEqual((created['status'], created['exam_version'], created['printed_number'], created['comments_copied']), ('imported', 'B', '5', 1))
        self.assertEqual(result['generation']['created'], ['5'])
        again = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], command)['results'][0]
        self.assertEqual((again['status'], again['comments_copied']), ('already_imported', 0))
        question = next(q for q in self.uploaded_questions() if q['exam_version'] == 'B')
        rules = question['rubric']['rules']
        kept = [line for line in lines if line['id'] != lines[2]['id']]
        self.assertEqual([i['id'] for i in rules['items']], [i['id'] for i in kept] + ['N1'])
        self.assertEqual(rules['items'][0]['description'], 'Full credit for $$y = 7$$')
        for before, after in zip(kept, rules['items']):
            self.assertEqual({k: v for k, v in after.items() if k not in ('description', 'original_source_text', 'exceptions')},
                             {k: v for k, v in before.items() if k not in ('description', 'original_source_text', 'exceptions')})
        self.assertEqual((rules['mode'], rules['floor'], rules['ceiling']), ('positive', 0, 10))
        self.assertIn("Adapted by a model from Version A's rubric", rules['warnings'][0])
        compiler = question['rubric']['compiler']
        self.assertEqual((compiler['name'], compiler['generation_id'], compiler['edited']), ('model-adapted-v1', generation['id'], True))
        self.assertEqual(compiler['adapted_from'], {'question_id': self.reference['question_id'], 'rubric_id': self.reference['rubric_id'],
                                                    'exam_version': 'A', 'printed_number': '18'})
        self.assertIsNone(question['rubric']['approved_at'])
        source = json.loads(question['rubric']['source_text'])
        self.assertEqual((source['adapted_from']['version'], source['adapted_from']['question']), ('Version A', '18'))
        _, original = self.inbox.read_artifact(question['rubric']['source_artifact_id'])
        self.assertEqual(original.decode(), question['rubric']['source_text'])
        with connect(self.path) as db:
            copied = db.execute('SELECT item_id,comment,rubric_id FROM frq_rubric_line_comments WHERE question_id=?', (question['question_id'],)).fetchall()
            self.assertEqual([tuple(r) for r in copied], [(lines[1]['id'], 'Accept any equivalent fraction.', question['rubric_id'])])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        # Version A is untouched, and the versions stay independent from here on.
        self.assertEqual(next(q for q in self.uploaded_questions() if q['exam_version'] == 'A')['rubric_id'], self.reference['rubric_id'])

    def accepted(self, generation, request_id='accept-1'):
        [draft] = generation['result']['questions']
        lines = [{'source': line['id'], 'description': line['description'], 'points': line['points']} for line in draft['items']]
        return {'request_id': request_id, 'copy_comments': True, 'questions': [self.keep(draft, items=lines, title='Question 5', printed_number='5')]}

    def confirmed_by(self):
        with connect(self.path) as db:
            return [(r['question_id'], r['actor']) for r in db.execute('SELECT question_id,actor FROM frq_rubric_review_ready WHERE assignment_id=? ORDER BY rowid', (self.plan['id'],))]

    def written_rows(self):
        with connect(self.path) as db:
            return {table: [tuple(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY rowid')] for table in (
                'frq_events', 'frq_rubric_review_ready', 'frq_rubric_generations', 'frq_rubric_quality_checks', 'frq_rubric_line_comments', 'frq_rubric_revisions')}

    def test_accepting_an_adaptation_readies_the_question_it_created_with_the_comments_it_copied(self):
        """ACCEPTING A DRAFT READIES IT (requirement, 2026-10-11): keeping an adapted question is its confirmation for the rubric
        review, made after the reference lines' comments were copied to it; its key is read without being asked once its answer area
        is confirmed, never at acceptance. The uploaded rubric it was adapted from keeps the instructor's own confirmation."""
        aid, lines = self.plan['id'], self.reference['rubric']['rules']['items']
        self.comment(self.reference, lines[1]['id'], 'Accept any equivalent fraction.')
        generation = self.adapt()
        command = self.accepted(generation)
        result = self.frq.accept_rubric_generation(aid, generation['id'], command)
        [created] = result['results']
        qid = created['question_id']
        self.assertEqual((created['status'], created['comments_copied'], result['readied']), ('imported', 1, [qid]))
        self.assertEqual(self.confirmed_by(), [(qid, 'draft-acceptance')])
        status = self.frq.rubric_quality(aid)
        self.assertEqual((status['readiness'][qid]['ready'], status['readiness'][qid]['comments']), (True, {lines[1]['id']: 'Accept any equivalent fraction.'}))
        self.assertEqual((status['readiness'][self.reference['question_id']]['ready'], status['unconfirmed']), (False, [self.reference['content']['title']]))
        # "Full credit" names no answer: the adapted question's key is to be read once its answer area is confirmed. Nothing is read now.
        self.assertEqual(result['keys_to_read'], [qid])
        with connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM frq_rubric_generations WHERE assignment_id=? AND json_extract(input_json,'$.kind')='key'", (aid,)).fetchone()[0], 0)
        self.assertEqual(result['review'], 'The rubric check starts by itself once every rubric is confirmed for review, the answer areas are confirmed '
                                           'and the answer keys, each read once its answer area is confirmed, are saved.')
        self.assertEqual(self.frq.assignment(aid)['rubric_review_waits'], result['review'])  # The plan read says the same while it waits.
        before = self.written_rows()
        again = self.frq.accept_rubric_generation(aid, generation['id'], command)
        self.assertEqual((again['results'][0]['status'], again['readied'], again['keys_to_read']), ('already_imported', [], [qid]))
        self.assertEqual(self.written_rows(), before)  # The same request again writes nothing more.

    def test_an_interrupted_acceptance_sent_again_confirms_only_a_question_still_as_it_left_it(self):
        aid, lines = self.plan['id'], self.reference['rubric']['rules']['items']
        self.comment(self.reference, lines[1]['id'], 'Accept any equivalent fraction.')
        generation = self.adapt()
        command = self.accepted(generation)
        with patch.object(self.frq, 'confirm_rubric_review', side_effect=Conflict('A rubric or its comments changed.')):
            result = self.frq.accept_rubric_generation(aid, generation['id'], command)
        [created] = result['results']
        self.assertEqual((created['status'], created['comments_copied'], result['readied'], self.confirmed_by()), ('imported', 1, [], []))
        # Sent again, the question was never confirmed and still holds the rubric and the copied comment: the acceptance finishes.
        again = self.frq.accept_rubric_generation(aid, generation['id'], command)
        self.assertEqual((again['results'][0]['status'], again['readied']), ('already_imported', [created['question_id']]))
        self.assertEqual(self.confirmed_by(), [(created['question_id'], 'draft-acceptance')])
        # A comment the instructor wrote since reopens it, and the acceptance sent once more does not confirm that for them.
        question = next(q for q in self.uploaded_questions() if q['question_id'] == created['question_id'])
        self.comment(question, lines[0]['id'], 'Written after the question was created.')
        before = self.written_rows()
        self.assertEqual(self.frq.accept_rubric_generation(aid, generation['id'], command)['readied'], [])
        self.assertEqual(self.written_rows(), before)
        self.assertFalse(self.frq.rubric_quality(aid)['readiness'][created['question_id']]['ready'])

    def test_a_changed_reference_rubric_refuses_the_stale_adaptation(self):
        generation = self.adapt()
        [draft] = generation['result']['questions']
        line = self.reference['rubric']['rules']['items'][1]
        self.frq.edit_rubric(self.plan['id'], self.reference['question_id'], {'version': self.reference['version'], 'rubric_id': self.reference['rubric_id'],
            'sha256': self.reference['rubric']['sha256'], 'lines': [{'item_id': line['id'], 'description': 'Reworded by the instructor'}]})
        self.assertEqual(self.frq.rubric_generation(self.plan['id'], generation['id'])['stale'], ['5'])
        result = self.frq.accept_rubric_generation(self.plan['id'], generation['id'], {'request_id': str(uuid.uuid4()), 'questions': [
            self.keep(draft, items=[{'source': i['id'], 'description': i['description'], 'points': i['points']} for i in draft['items']])]})
        self.assertEqual(result['results'][0]['status'], 'failed')
        self.assertIn('changed after it was adapted', result['results'][0]['error'])
        self.assertEqual(len(self.uploaded_questions()), 1)

    def test_an_adaptation_that_leaves_out_a_line_is_asked_again_then_reported(self):
        self.drop = (self.reference['rubric']['rules']['items'][-1]['id'],)
        generation = self.adapt()
        self.assertEqual(generation['status'], 'failed')
        self.assertIn('No problem could be adapted', generation['error'])
        self.assertEqual(len([c for c in self.calls(generation['id']) if c['step'] == 'rubric_adapt:q5']), 2)
        # A pairing that names a reference problem twice is refused the same way.
        self.drop, self.pairs = (), {'5': '18', '18': '18'}
        generation = self.adapt()
        self.assertEqual(generation['status'], 'failed')
        self.assertIn('could not be paired', generation['error'])
        self.assertEqual(len([c for c in self.calls(generation['id']) if c['step'] == 'rubric_adapt:match']), 2)

    def test_the_request_is_checked_before_anything_is_queued(self):
        slots = self.slots()
        base = {'template_id': self.blank['id'], 'request_id': str(uuid.uuid4())}
        with self.assertRaisesRegex(ValueError, 'another version'):
            self.frq.queue_rubric_generation(self.plan['id'], {**base, 'key': slots['A']['key'], 'adapt_from': slots['A']['key']})
        with self.assertRaisesRegex(ValueError, 'no rubric'):
            self.frq.queue_rubric_generation(self.plan['id'], {**base, 'key': slots['A']['key'], 'adapt_from': slots['B']['key']})
        with self.assertRaisesRegex(ValueError, 'blank template'):
            self.frq.queue_rubric_generation(self.plan['id'], {**base, 'key': slots['B']['key'], 'adapt_from': slots['A']['key'], 'template_id': 'nope'})
        self.configure('beta-text')
        with self.assertRaisesRegex(ValueError, 'cannot read images'):
            self.frq.queue_rubric_generation(self.plan['id'], {**base, 'key': slots['B']['key'], 'adapt_from': slots['A']['key']})
        with connect(self.path) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM frq_rubric_generations').fetchone()[0], 0)

    def test_a_finding_names_only_what_the_original_left_in_a_line(self):
        """GRADING "LEFTOVER VALUES": Quiz 5's Version E was told its union rule "names values". A formula, or a value on both versions, says nothing."""
        reference = {'statement': 'Given $$P(A^C)=0.45$$, $$P(B)=0.40$$, $$P(A\\cup B)=0.75$$.', 'solution': 'P(A)=0.55, $$P(A\\cap B)=0.20$$', 'points': 2,
                     'final_answers': [{'part': 'a', 'answer': '0.20 or 1/5'}], 'rubric': {'items': []}}
        problem = {'points': 2, 'parts': [], 'statement': 'Given $$P(A^C)=0.38$$, $$P(B)=0.42$$, $$P(A\\cup B)=0.86$$.'}
        answers = [{'part': 'a', 'answer': '0.18'}]

        def found(line, before=None):
            return adaptation_warnings(problem, {**reference, 'rubric': {'items': [{'id': 'R3', 'description': before or line}]}}, [{'id': 'R3', 'description': line}],
                                       'Version A', '$$P(A\\cap B)=0.18$$', answers)
        self.assertEqual(found('Part A: Union rule $$P(A\\cup B)=P(A)+P(B)-P(A\\cap B)$$ missing or misapplied'), [])
        self.assertEqual(found('Value of $$P(A\\cap B)$$ missing'), [])
        self.assertEqual(found('Value of $$P(A\\cap B)=0.18$$ missing', 'Value of $$P(A\\cap B)=0.20$$ missing'), [])  # rewritten for this version
        self.assertEqual(found('Value of $$P(A\\cap B)=0.20$$ missing'),
                         ["R3 (P(A\\capB)=0.20) still names Version A's values, which this version's problem and answers do not have: check it."])
        self.assertEqual(found('Part A: not 0.20 as printed.'),
                         ["R3 (0.2) still names Version A's values, which this version's problem and answers do not have: check it."])
        # Rewritten around a value left behind.
        self.assertIn('R3 (0.2)', found('Accept $$0.18$$ but never 0.20.', 'Accept $$0.20$$ but never 0.20.')[0])
        # A correct choice's letter is printed on both versions, so it is read against this version's answers.
        self.assertIn('R3 ((b))', adaptation_warnings(
            {**problem, 'statement': '(a) one (b) two (c) three'}, {'statement': '(a) one (b) two', 'solution': '', 'points': 2, 'final_answers': [{'part': '', 'answer': '(b)'}],
                                                                   'rubric': {'items': [{'id': 'R3', 'description': 'Correct choice: $$(b)$$'}]}},
            [{'id': 'R3', 'description': 'Correct choice: $$(b)$$'}], 'Version A', '', [{'part': '', 'answer': '(c)'}])[0])

    def test_the_reference_structure_survives_dropped_lines_and_moved_conditions(self):
        source = {'mode': 'positive', 'floor': 0, 'ceiling': 8, 'groups': [], 'notes': '', 'warnings': [], 'items': [
            {'id': 'R1', 'description': 'Full credit', 'points': 8, 'group': None, 'follow_through': False, 'repeat_penalty_for_same_originating_error': True,
             'stackable': True, 'requires': [], 'excludes': ['R2', 'R3'], 'exceptions': '', 'notes': '', 'original_source_text': 'Full credit'},
            {'id': 'R2', 'description': 'Correct setup, only if R3 is not applied', 'points': 4, 'group': None, 'follow_through': True,
             'repeat_penalty_for_same_originating_error': False, 'stackable': True, 'requires': [], 'excludes': ['R3'],
             'exceptions': 'Correct setup, only if R3 is not applied', 'notes': '', 'original_source_text': 'x', 'condition_handling': 'structured'},
            {'id': 'R3', 'description': 'Answer follows from setup', 'points': 4, 'group': None, 'follow_through': True,
             'repeat_penalty_for_same_originating_error': False, 'stackable': True, 'requires': ['R2'], 'excludes': [], 'exceptions': '', 'notes': '', 'original_source_text': 'y'}]}
        warnings = []
        rules = adapted_rules(source, [{'source': 'R1', 'description': 'Full credit for $$y=7$$', 'points': 8},
                                       {'source': 'R2', 'description': 'Correct setup for $$3y$$, only if R3 is not applied', 'points': 4},
                                       {'source': 'R3', 'description': '', 'points': 4}], 8, 8, warnings)
        self.assertEqual([i['id'] for i in rules['items']], ['R1', 'R2'])
        self.assertEqual((rules['items'][0]['excludes'], rules['items'][1]['excludes']), (['R2'], []))
        self.assertTrue(rules['items'][1]['follow_through'])
        self.assertEqual(rules['items'][1]['exceptions'], 'Correct setup for $$3y$$, only if R3 is not applied')  # The importer's mark follows the wording.
        self.assertNotIn('condition_handling', rules['items'][1])  # Its exclusion is gone, so the instructor confirms the condition again.
        self.assertIn('R2: the structured condition', warnings[0])
        with self.assertRaisesRegex(ValueError, 'once'):
            adapted_rules(source, [{'source': 'R1', 'description': 'a', 'points': 8}, {'source': 'R1', 'description': 'b', 'points': 8}], 8, 8, [])
        with self.assertRaisesRegex(ValueError, 'Line points'):
            adapted_rules(source, [{'source': 'R1', 'description': 'a', 'points': -1}], 8, 8, [])


if __name__ == '__main__':
    unittest.main()
